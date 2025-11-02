from contextlib import asynccontextmanager
import asyncio
import logging
import os
import stat
from typing import Dict, Optional
from urllib.parse import urljoin
from fastapi import FastAPI, HTTPException, Request, Response, Header, Depends
from fastapi.responses import ORJSONResponse
from loguru import logger
from sek8s.config import AttestationProxyConfig
from sek8s.server import WebServer
import httpx
import backoff
import time
from functools import lru_cache
from bittensor import Keypair
from substrateinterface import KeypairType
import uvicorn

# Configuration
SERVICE_NAMESPACE = os.getenv("WORKLOAD_NAMESPACE", "chutes")
CLUSTER_DOMAIN = "svc.cluster.local"
SOCKET_PATH = "/var/run/attestation/attestation.sock"
MAX_CONSECUTIVE_FAILURES = 5

# Port configuration
EXTERNAL_PORT = int(os.getenv("EXTERNAL_PORT", "8443"))
INTERNAL_PORT = int(os.getenv("INTERNAL_PORT", "8444"))

# Header names
VALIDATOR_HEADER = "X-Chutes-Validator"
HOTKEY_HEADER = "X-Chutes-Hotkey"
NONCE_HEADER = "X-Chutes-Nonce"
SIGNATURE_HEADER = "X-Chutes-Signature"
NONCE_MAX_AGE_SECONDS = 30


@lru_cache(maxsize=2)
def get_keypair(ss58: str) -> Keypair:
    """Helper to load keypairs efficiently."""
    return Keypair(ss58_address=ss58, crypto_type=KeypairType.SR25519)


class AttestationProxyServer:
    """Async web server for attestation proxy with dual-port support."""

    def __init__(self, config: AttestationProxyConfig):
        self.config = config

        self.app = FastAPI(
            debug=config.debug,
            default_response_class=ORJSONResponse, 
            lifespan=self._lifespan
        )
        
        self.unix_client: Optional[httpx.AsyncClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.unix_client_lock = asyncio.Lock()
        self.consecutive_socket_failures = 0
        
        # Load validator hotkeys
        validator_hotkeys_str = os.getenv("ALLOWED_VALIDATOR_HOTKEYS", "")
        self.allowed_validator_hotkeys = [
            hk.strip() for hk in validator_hotkeys_str.split(",") if hk.strip()
        ]
        
        if not self.allowed_validator_hotkeys:
            logger.warning("No validator hotkeys configured - external auth will fail")
        else:
            logger.info(f"Configured {len(self.allowed_validator_hotkeys)} allowed validator(s)")
        
        self._setup_routes()

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """Initialize HTTP clients on startup"""
        
        # Add middleware to compute body SHA256 for signature verification
        @app.middleware("http")
        async def add_body_sha256(request: Request, call_next):
            """Compute SHA256 of request body for signature verification"""
            if request.method in ["POST", "PUT", "PATCH"]:
                body = await request.body()
                if body:
                    import hashlib
                    request.state.body_sha256 = hashlib.sha256(body).hexdigest()
                else:
                    request.state.body_sha256 = None
            else:
                request.state.body_sha256 = None
            
            response = await call_next(request)
            return response
        
        # Client for K8s service communication
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            verify=False
        )
        
        try:
            self.unix_client = self._create_unix_client()
            logger.info("Unix socket client initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to initialize Unix socket client: {e}")
        
        logger.info(f"Attestation proxy started - External port: {EXTERNAL_PORT}, Internal port: {INTERNAL_PORT}")
        yield
        
        if self.unix_client:
            await self.unix_client.aclose()
        if self.http_client:
            await self.http_client.aclose()
        
        logger.info("Attestation proxy shutdown complete")

    def _create_unix_client(self) -> httpx.AsyncClient:
        """Create a new Unix socket client"""
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=SOCKET_PATH),
            base_url="http://localhost",
            timeout=httpx.Timeout(30.0)
        )
    
    def _is_valid_socket(self) -> bool:
        """Check if socket path exists and is a valid socket file"""
        try:
            if not os.path.exists(SOCKET_PATH):
                return False
            stat_info = os.stat(SOCKET_PATH)
            return stat.S_ISSOCK(stat_info.st_mode)
        except OSError as e:
            logger.warning(f"Error checking socket {SOCKET_PATH}: {e}")
            return False

    def _setup_routes(self):
        """Setup web routes."""
        
        # Health check (no auth)
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        
        # Server routes - proxy to host attestation service
        self.app.add_api_route(
            "/server/{path:path}",
            self.proxy_to_host_service,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        # Service routes - proxy to workload services
        self.app.add_api_route(
            "/service/{service_name}/{path:path}",
            self.proxy_to_service,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        self.app.add_exception_handler(404, self.not_found_handler)

    async def health_check(self):
        """
        Health check endpoint
        Returns unhealthy immediately if socket is invalid (stale mount) or after consecutive failures
        """
        socket_valid = self._is_valid_socket()
        too_many_failures = self.consecutive_socket_failures >= MAX_CONSECUTIVE_FAILURES
        
        if not socket_valid:
            logger.error(f"Health check failed: Unix socket invalid at {SOCKET_PATH}")
            return Response(
                content="unhealthy: unix socket unavailable",
                status_code=503,
                media_type="text/plain"
            )
        
        if too_many_failures:
            logger.error(f"Health check failed: {self.consecutive_socket_failures} consecutive failures")
            return Response(
                content=f"unhealthy: {self.consecutive_socket_failures} consecutive socket failures",
                status_code=503,
                media_type="text/plain"
            )
        
        return {
            "status": "healthy",
            "service": "attestation-proxy",
            "socket_valid": socket_valid,
            "consecutive_failures": self.consecutive_socket_failures
        }
    
    async def not_found_handler(self, request: Request, exc):
        """Custom 404 handler"""
        return Response(
            content=f"Proxy route not found: {request.url.path}",
            status_code=404,
            media_type="text/plain"
        )

    # ========================================================================
    # AUTHENTICATION
    # ========================================================================

    async def verify_auth(
        self,
        request: Request,
        validator: Optional[str] = Header(None, alias=VALIDATOR_HEADER),
        nonce: Optional[str] = Header(None, alias=NONCE_HEADER),
        signature: Optional[str] = Header(None, alias=SIGNATURE_HEADER),
    ):
        """
        Verify authentication based on which port the request came in on:
        - External port (8443): Require validator signature
        - Internal port (8444): No auth (NetworkPolicy enforced)
        """
        
        # Get the port this request came in on
        server_info = request.scope.get("server")
        if not server_info or len(server_info) < 2:
            logger.error("Unable to determine server port from request")
            raise HTTPException(
                status_code=500,
                detail="Unable to determine request port"
            )
        
        server_port = server_info[1]
        
        if server_port == EXTERNAL_PORT:
            # External port - require validator signature
            logger.debug(f"Request on external port {EXTERNAL_PORT} - validating signature")
            
            if not all([validator, nonce, signature]):
                logger.warning(
                    f"External request missing signature headers: "
                    f"validator={bool(validator)}, nonce={bool(nonce)}, signature={bool(signature)}"
                )
                raise HTTPException(
                    status_code=401,
                    detail="External requests require: X-Validator-Hotkey, X-Nonce, X-Signature"
                )
            
            return await self._verify_validator_signature(
                request=request,
                validator=validator,
                nonce=nonce,
                signature=signature
            )
        
        elif server_port == INTERNAL_PORT:
            # Internal port - no auth required (NetworkPolicy enforces access)
            logger.debug(f"Request on internal port {INTERNAL_PORT} - no auth required (NetworkPolicy enforced)")
            return True
        
        else:
            # Unknown port - should never happen
            logger.error(f"Request on unexpected port: {server_port}")
            raise HTTPException(
                status_code=403,
                detail=f"Request on invalid port: {server_port}"
            )

    async def _verify_validator_signature(
        self,
        request: Request,
        validator: str,
        nonce: str,
        signature: str,
    ) -> bool:
        """Verify Bittensor validator signature for external requests."""
        
        # Verify validator is allowed
        if validator not in self.allowed_validator_hotkeys:
            logger.warning(f"Unauthorized validator attempted access: {validator}")
            raise HTTPException(
                status_code=403,
                detail="Validator not authorized"
            )
        
        # Verify nonce is recent (prevent replay attacks)
        try:
            nonce_timestamp = int(nonce)
            current_time = int(time.time())
            age = current_time - nonce_timestamp
            
            if age >= NONCE_MAX_AGE_SECONDS:
                logger.warning(
                    f"Expired nonce from validator {validator}: "
                    f"age={age}s, max={NONCE_MAX_AGE_SECONDS}s"
                )
                raise HTTPException(
                    status_code=401,
                    detail=f"Nonce expired (age: {age}s, max: {NONCE_MAX_AGE_SECONDS}s)"
                )
            
            if age < 0:
                logger.warning(f"Future nonce from validator {validator}: {nonce}")
                raise HTTPException(
                    status_code=401,
                    detail="Invalid nonce (future timestamp)"
                )
                
        except ValueError:
            logger.warning(f"Invalid nonce format from validator {validator}: {nonce}")
            raise HTTPException(
                status_code=401,
                detail="Invalid nonce format (must be Unix timestamp)"
            )
        
        # Build signature string
        if hasattr(request.state, 'body_sha256') and request.state.body_sha256:
            purpose = request.state.body_sha256
        else:
            purpose = request.url.path
        
        signature_string = ":".join([validator, nonce, purpose])
        
        # Verify signature
        try:
            keypair = get_keypair(validator)
            signature_bytes = bytes.fromhex(signature)
            
            if not keypair.verify(signature_string, signature_bytes):
                logger.warning(
                    f"Invalid signature from validator {validator}: "
                    f"signature_string='{signature_string}'"
                )
                raise HTTPException(
                    status_code=401,
                    detail="Invalid signature"
                )
            
            logger.info(f"Successfully authenticated validator: {validator}")
            return True
            
        except ValueError as e:
            logger.error(f"Signature hex decode error for validator {validator}: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid signature format (must be hex)"
            )
        except Exception as e:
            logger.error(f"Signature verification error for validator {validator}: {e}")
            raise HTTPException(
                status_code=401,
                detail="Signature verification failed"
            )

    # ========================================================================
    # PROXY ENDPOINTS
    # ========================================================================

    async def proxy_to_host_service(
        self,
        path: str,
        request: Request,
        _auth: bool = Depends(verify_auth)
    ):
        """
        Proxy requests to host attestation service via Unix socket
        /server/quote -> GET /quote to localhost service
        """
        method = request.method
        body = await request.body()
        params = dict(request.query_params)
        headers = self.extract_client_cert_info(request)
        
        # Add original request headers
        for key, value in request.headers.items():
            if key.lower() not in ["host", "content-length"]:
                headers[key] = value
        
        return await self.proxy_request(
            target_url="http://localhost",
            method=method,
            path=f"/{path}",
            headers=headers,
            body=body,
            params=params,
            use_unix_socket=True
        )

    async def proxy_to_service(
        self,
        service_name: str,
        path: str,
        request: Request,
        _auth: bool = Depends(verify_auth)
    ):
        """
        Proxy requests to K8s workload services
        /service/chute-abcd-123/_get_devices -> GET /_get_devices to chute-abcd-123.chutes.svc.cluster.local
        """
        # Validate service name (basic security)
        if not service_name.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(
                status_code=400,
                detail="Invalid service name"
            )
        
        method = request.method
        body = await request.body()
        params = dict(request.query_params)
        headers = self.extract_client_cert_info(request)
        
        # Add original request headers
        for key, value in request.headers.items():
            if key.lower() not in ["host", "content-length"]:
                headers[key] = value
        
        # Build K8s service URL
        service_url = f"http://{service_name}.{SERVICE_NAMESPACE}.{CLUSTER_DOMAIN}"
        
        return await self.proxy_request(
            target_url=service_url,
            method=method,
            path=f"/{path}",
            headers=headers,
            body=body,
            params=params,
            use_unix_socket=False
        )

    # ========================================================================
    # SHARED PROXY LOGIC
    # ========================================================================

    @backoff.on_exception(
        backoff.expo,
        httpx.ConnectError,
        max_tries=2,
        max_time=5
    )
    async def proxy_request(
        self,
        target_url: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: bytes = b"",
        params: Dict[str, str] = None,
        use_unix_socket: bool = False
    ) -> Response:
        """Proxy request with automatic retry on connection errors."""
        
        client = self.unix_client if use_unix_socket else self.http_client
        full_url = urljoin(target_url, path)
        
        # Filter hop-by-hop headers
        filtered_headers = {
            k: v for k, v in headers.items() 
            if k.lower() not in [
                "host", "connection", "upgrade", "proxy-authenticate",
                "proxy-authorization", "te", "trailers", "transfer-encoding"
            ]
        }
        
        try:
            logger.info(f"Proxying {method} {full_url}")
            
            response = await client.request(
                method=method,
                url=full_url,
                headers=filtered_headers,
                content=body,
                params=params,
                follow_redirects=False
            )
            
            # Reset failure counter on success
            if use_unix_socket:
                self.consecutive_socket_failures = 0
            
            # Filter response headers
            response_headers = {
                k: v for k, v in response.headers.items()
                if k.lower() not in [
                    "connection", "upgrade", "proxy-authenticate",
                    "proxy-authorization", "te", "trailers", "transfer-encoding"
                ]
            }
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type")
            )
            
        except httpx.ConnectError as e:
            logger.error(f"Connection failed to {full_url}: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
                logger.warning(
                    f"Unix socket connection failed ({self.consecutive_socket_failures} consecutive failures). "
                    f"Health check will trigger pod restart at {MAX_CONSECUTIVE_FAILURES} failures."
                )
            raise  # Let backoff handle retry
        except httpx.RequestError as e:
            logger.error(f"Request failed to {full_url}: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
            raise HTTPException(
                status_code=502,
                detail=f"Proxy request failed: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error proxying to {full_url}: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
            raise HTTPException(
                status_code=500,
                detail=f"Internal proxy error: {str(e)}"
            )
    
    def extract_client_cert_info(self, request: Request) -> Dict[str, str]:
        """Extract client certificate information from headers"""
        return {
            "X-Client-Cert": request.headers.get("X-Client-Cert", ""),
            "X-Client-Verify": request.headers.get("X-Client-Verify", ""),
            "X-Client-S-DN": request.headers.get("X-Client-S-DN", ""),
            "X-Client-I-DN": request.headers.get("X-Client-I-DN", ""),
            "X-Real-IP": request.headers.get("X-Real-IP", ""),
            "X-Forwarded-For": request.headers.get("X-Forwarded-For", ""),
            "X-Forwarded-Proto": request.headers.get("X-Forwarded-Proto", ""),
        }

    async def run_server(self, port: int):
        """Run a single Uvicorn server instance on specified port"""
        config = uvicorn.Config(
            self.app,
            host=self.config.bind_address,
            port=port,
            ssl_keyfile=self.config.tls_key_path,
            ssl_certfile=self.config.tls_cert_path,
            log_level="info" if not self.config.debug else "debug",
            access_log=True,
        )
        server = uvicorn.Server(config)
        logger.info(f"Starting server on port {port} (TLS: {bool(self.config.tls_key_path)})")
        await server.serve()

    async def run_dual_port(self):
        """Run servers on both external and internal ports concurrently"""
        logger.info(
            f"Starting attestation proxy with dual ports:\n"
            f"  - External port {EXTERNAL_PORT}: Validator signature required\n"
            f"  - Internal port {INTERNAL_PORT}: NetworkPolicy enforced, no auth"
        )
        
        # Run both servers concurrently
        await asyncio.gather(
            self.run_server(
                port=EXTERNAL_PORT
            ),
            self.run_server(
                port=INTERNAL_PORT
            )
        )


def run():
    """Main entry point."""
    try:
        # Load configuration
        config = AttestationProxyConfig()

        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")

        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        # Create and run server
        server = AttestationProxyServer(config)
        
        # Run with asyncio
        asyncio.run(server.run_dual_port())

    except Exception as e:
        logger.exception("Failed to start Attestation proxy service: %s", e)
        raise


if __name__ == "__main__":
    run()