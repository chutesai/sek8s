from contextlib import asynccontextmanager
import asyncio
import logging
import os
import stat
from typing import Dict, Optional
from urllib.parse import urljoin
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from loguru import logger
from sek8s.config import AttestationProxyConfig
from sek8s.server import WebServer
import httpx
import backoff

from sek8s.services.util import authorize


# Configuration
SERVICE_NAMESPACE = os.getenv("WORKLOAD_NAMESPACE", "chutes")
CLUSTER_DOMAIN = "svc.cluster.local"
SOCKET_PATH = "/var/run/attestation/attestation.sock"
MAX_CONSECUTIVE_FAILURES = 5

# Port configuration
EXTERNAL_PORT = int(os.getenv("EXTERNAL_PORT", "8443"))
INTERNAL_PORT = int(os.getenv("INTERNAL_PORT", "8444"))


class SharedProxyResources:
    """Shared resources used by both internal and external proxy servers."""
    
    def __init__(self):
        self.unix_client: Optional[httpx.AsyncClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.consecutive_socket_failures = 0
        self._initialized = False
        self._lock = asyncio.Lock()
    
    async def initialize(self):
        """Initialize shared HTTP clients (idempotent)"""
        async with self._lock:
            if self._initialized:
                logger.debug("Shared resources already initialized, skipping")
                return
            
            logger.info("Initializing shared proxy resources...")
            
            # Client for K8s service communication
            self.http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                verify=False
            )
            
            try:
                self.unix_client = httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(uds=SOCKET_PATH),
                    base_url="http://localhost",
                    timeout=httpx.Timeout(30.0)
                )
                logger.info("Unix socket client initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize Unix socket client: {e}")
            
            self._initialized = True
            logger.info("Shared proxy resources initialized")
    
    async def cleanup(self):
        """Cleanup shared HTTP clients"""
        async with self._lock:
            if not self._initialized:
                return
            
            if self.unix_client:
                await self.unix_client.aclose()
            if self.http_client:
                await self.http_client.aclose()
            
            self._initialized = False
            logger.info("Shared proxy resources cleaned up")
    
    def is_valid_socket(self) -> bool:
        """Check if socket path exists and is a valid socket file"""
        try:
            if not os.path.exists(SOCKET_PATH):
                return False
            stat_info = os.stat(SOCKET_PATH)
            return stat.S_ISSOCK(stat_info.st_mode)
        except OSError as e:
            logger.warning(f"Error checking socket {SOCKET_PATH}: {e}")
            return False


class BaseProxyServer(WebServer):
    """Base proxy server with shared functionality."""
    
    def __init__(self, config: AttestationProxyConfig, shared_resources: SharedProxyResources, server_name: str):
        self.shared = shared_resources
        self.server_name = server_name
        
        # Create lifespan that initializes shared resources
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info(f"[{self.server_name}] Lifespan starting...")
            await self.shared.initialize()
            logger.info(f"[{self.server_name}] Lifespan startup complete")
            yield
            logger.info(f"[{self.server_name}] Lifespan shutdown...")
            # Don't cleanup here - let orchestrator handle it
        
        super().__init__(config, lifespan=lifespan)
    
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
        
        client = self.shared.unix_client if use_unix_socket else self.shared.http_client
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
                self.shared.consecutive_socket_failures = 0
            
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
                self.shared.consecutive_socket_failures += 1
                logger.warning(
                    f"Unix socket connection failed ({self.shared.consecutive_socket_failures} consecutive failures). "
                    f"Health check will trigger pod restart at {MAX_CONSECUTIVE_FAILURES} failures."
                )
            raise  # Let backoff handle retry
        except httpx.RequestError as e:
            logger.error(f"Request failed to {full_url}: {e}")
            if use_unix_socket:
                self.shared.consecutive_socket_failures += 1
            raise HTTPException(
                status_code=502,
                detail=f"Proxy request failed: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error proxying to {full_url}: {e}")
            if use_unix_socket:
                self.shared.consecutive_socket_failures += 1
            raise HTTPException(
                status_code=500,
                detail=f"Internal proxy error: {str(e)}"
            )
    
    async def health_check(self):
        """Health check endpoint"""
        socket_valid = self.shared.is_valid_socket()
        too_many_failures = self.shared.consecutive_socket_failures >= MAX_CONSECUTIVE_FAILURES
        
        if not socket_valid:
            logger.error(f"Health check failed: Unix socket invalid at {SOCKET_PATH}")
            return Response(
                content="unhealthy: unix socket unavailable",
                status_code=503,
                media_type="text/plain"
            )
        
        if too_many_failures:
            logger.error(f"Health check failed: {self.shared.consecutive_socket_failures} consecutive failures")
            return Response(
                content=f"unhealthy: {self.shared.consecutive_socket_failures} consecutive socket failures",
                status_code=503,
                media_type="text/plain"
            )
        
        return {
            "status": "healthy",
            "service": "attestation-proxy",
            "socket_valid": socket_valid,
            "consecutive_failures": self.shared.consecutive_socket_failures
        }
    
    async def not_found_handler(self, request: Request, exc):
        """Custom 404 handler"""
        return Response(
            content=f"Proxy route not found: {request.url.path}",
            status_code=404,
            media_type="text/plain"
        )
    
    async def proxy_to_host_service(self, path: str, request: Request):
        """Proxy requests to host attestation service via Unix socket"""
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
    
    async def proxy_to_service(self, service_name: str, path: str, request: Request):
        """Proxy requests to K8s workload services"""
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


class ExternalProxyServer(BaseProxyServer):
    """External-facing proxy server with validator signature authentication."""
    
    def __init__(self, config: AttestationProxyConfig, shared_resources: SharedProxyResources):        
        super().__init__(config, shared_resources, "EXTERNAL")
    
    def _setup_routes(self):
        """Setup routes with validator authentication."""
        
        # Middleware to compute body SHA256 for signature verification
        @self.app.middleware("http")
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
        
        # Health check (no auth)
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        
        # Server health check (no auth)
        self.app.add_api_route(
            "/server/health",
            self.proxy_to_host_service_health,
            methods=["GET"]
        )

        # Allow miner and validator to retrieve devices
        self.app.add_api_route(
            "/server/devices",
            self.proxy_devices_authenticated,
            methods=["GET"]
        )

        # Protected routes with validator auth
        self.app.add_api_route(
            "/server/{path:path}",
            self.proxy_to_host_service_authenticated,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        self.app.add_api_route(
            "/service/{service_name}/{path:path}",
            self.proxy_to_service_authenticated,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        self.app.add_exception_handler(404, self.not_found_handler)
        
        logger.info(f"External server routes configured (port {EXTERNAL_PORT})")
    
    async def proxy_to_host_service_health(self, request: Request):
        """Proxy health check to host service without authentication"""
        return await self.proxy_to_host_service(path="health", request=request)
    
    async def proxy_devices_authenticated(
        self,
        request: Request,
        _auth: bool = Depends(authorize(allow_miner=True, allow_validator=True, purpose="attest"))    
    ):
        return await self.proxy_to_host_service(path="devices", request=request)

    async def proxy_to_host_service_authenticated(
        self,
        path: str,
        request: Request,
        _auth: bool = Depends(authorize(allow_validator=True, purpose="attest"))
    ):
        """Proxy to host service with validator auth"""
        return await self.proxy_to_host_service(path, request)
    
    async def proxy_to_service_authenticated(
        self,
        service_name: str,
        path: str,
        request: Request,
        _auth: bool = Depends(authorize(allow_validator=True, purpose="attest"))
    ):
        """Proxy to K8s service with validator auth"""
        return await self.proxy_to_service(service_name, path, request)


class InternalProxyServer(BaseProxyServer):
    """Internal proxy server with no authentication (NetworkPolicy enforced)."""

    def __init__(self, config: AttestationProxyConfig, shared_resources: SharedProxyResources):
        
        super().__init__(config, shared_resources, "INTERNAL")
    
    def _setup_routes(self):
        """Setup routes with no authentication."""
        
        # Health check
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        
        # Unprotected routes (NetworkPolicy enforces access control)
        self.app.add_api_route(
            "/server/{path:path}",
            self.proxy_to_host_service,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        self.app.add_api_route(
            "/service/{service_name}/{path:path}",
            self.proxy_to_service,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        
        self.app.add_exception_handler(404, self.not_found_handler)
        
        logger.info(f"Internal server routes configured (port {INTERNAL_PORT})")


async def run_server_async(server_instance: BaseProxyServer, port: int, config: AttestationProxyConfig):
    """Run a server using uvicorn.Server for async support"""
    import uvicorn
    
    server_name = server_instance.server_name
    logger.info(f"[{server_name}] Preparing to start on {config.bind_address}:{port}")
    
    uvicorn_config = uvicorn.Config(
        server_instance.app,
        host=config.bind_address,
        port=port,
        ssl_keyfile=config.tls_key_path,
        ssl_certfile=config.tls_cert_path,
        log_level="debug" if config.debug else "info",
    )
    server = uvicorn.Server(uvicorn_config)
    
    logger.info(f"[{server_name}] Starting uvicorn server on port {port}")
    await server.serve()
    logger.info(f"[{server_name}] Server stopped on port {port}")


def run():
    """Main entry point."""
    try:
        # Suppress OpenBLAS warning
        os.environ['OPENBLAS_NUM_THREADS'] = '1'
        
        # Load configuration
        config = AttestationProxyConfig()

        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")

        # Create shared resources
        shared_resources = SharedProxyResources()
        
        # Create external server config (port 8443)
        external_config = AttestationProxyConfig()
        external_config.port = EXTERNAL_PORT
        external_server = ExternalProxyServer(external_config, shared_resources)
        
        # Create internal server config (port 8444)
        internal_config = AttestationProxyConfig()
        internal_config.port = INTERNAL_PORT
        internal_server = InternalProxyServer(internal_config, shared_resources)
        
        logger.info(
            f"Starting attestation proxy with dual ports:\n"
            f"  - External port {EXTERNAL_PORT}: Validator signature required\n"
            f"  - Internal port {INTERNAL_PORT}: NetworkPolicy enforced, no auth"
        )
        
        # Run both servers concurrently
        async def run_both():
            try:
                logger.info("Launching both servers concurrently...")
                # Run both servers concurrently
                await asyncio.gather(
                    run_server_async(external_server, EXTERNAL_PORT, external_config),
                    run_server_async(internal_server, INTERNAL_PORT, internal_config)
                )
            except Exception as e:
                logger.exception(f"Error running servers: {e}")
                raise
            finally:
                # Cleanup shared resources
                await shared_resources.cleanup()
                logger.info("Attestation proxy shutdown complete")
        
        asyncio.run(run_both())

    except Exception as e:
        logger.exception("Failed to start Attestation proxy service: %s", e)
        raise


if __name__ == "__main__":
    run()