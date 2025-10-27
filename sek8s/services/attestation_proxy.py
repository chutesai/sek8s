from contextlib import asynccontextmanager
import logging
import os
from typing import Dict, Optional
from urllib.parse import urljoin
from fastapi import FastAPI, HTTPException, Request, Response
from loguru import logger
from sek8s.config import AttestationProxyConfig
from sek8s.server import WebServer
import httpx
import asyncio
import backoff

# Configuration
SERVICE_NAMESPACE = "chutes"
CLUSTER_DOMAIN = "svc.cluster.local"
SOCKET_PATH = "/var/run/attestation/attestation.sock"
MAX_CONSECUTIVE_FAILURES = 5  # Fail health check after this many failures


class AttestationProxyServer(WebServer):
    """Async web server for attestation proxy with resilient Unix socket handling."""

    def __init__(self, config: AttestationProxyConfig):
        super().__init__(config, lifespan=self._lifespan)
        
        # Instance variables for clients
        self.unix_client: Optional[httpx.AsyncClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.unix_client_lock = asyncio.Lock()
        self.consecutive_socket_failures = 0

    @asynccontextmanager
    async def _lifespan(self, _: FastAPI):
        """Initialize HTTP clients on startup"""
        
        # Client for K8s service communication (workloads)
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            verify=False  # Internal cluster communication
        )
        
        try:
            self.unix_client = self._create_unix_client()
            logger.info("Unix socket client initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to initialize Unix socket client: {e}")
        
        logger.info("Attestation proxy started successfully")

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

    async def _recreate_unix_client_on_error(self):
        """Force recreation of Unix client after an error"""
        async with self.unix_client_lock:
            if self.unix_client:
                logger.info("Closing stale Unix socket client")
                try:
                    await self.unix_client.aclose()
                except Exception as e:
                    logger.warning(f"Error closing stale client: {e}")

            self.unix_client = self._create_unix_client()

    def _setup_routes(self):
        """Setup web routes."""
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        self.app.add_exception_handler(404, self.not_found_handler)
        self.app.add_api_route("/server/{path:path}", self.proxy_to_host_service, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        self.app.add_api_route("/service/{service_name}/{path:path}", self.proxy_to_service, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])

    async def health_check(self):
        """
        Health check endpoint
        Returns unhealthy if too many consecutive socket failures
        """
        socket_exists = os.path.exists(SOCKET_PATH)
        too_many_failures = self.consecutive_socket_failures >= MAX_CONSECUTIVE_FAILURES
        
        status = "healthy"
        if too_many_failures:
            status = "unhealthy"
        elif not socket_exists:
            status = "degraded"
        
        return {
            "status": status,
            "service": "attestation-proxy",
            "socket_exists": socket_exists,
            "unix_client_active": self.unix_client is not None,
            "http_client_active": self.http_client is not None,
            "consecutive_failures": self.consecutive_socket_failures,
            "details": "Socket unavailable" if not socket_exists else None
        }
    
    async def not_found_handler(self, request: Request, exc):
        """Custom 404 handler"""
        return Response(
            content=f"Proxy route not found: {request.url.path}",
            status_code=404,
            media_type="text/plain"
        )

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
        """
        Proxy a request to the target service with automatic retry on connection errors.
        For Unix socket requests, automatically recreates the client on connection failure.
        """
        # Determine which client to use
        client = self.unix_client if use_unix_socket else self.http_client
        
        # Build full URL
        full_url = urljoin(target_url, path)
        
        # Filter headers (remove hop-by-hop headers)
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
            
            # Return response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
                media_type=response.headers.get("content-type")
            )
            
        except httpx.ConnectError as e:
            logger.error(f"Connection failed: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
                # Recreate client before backoff retries
                await self._recreate_unix_client_on_error()
            raise  # Let backoff handle the retry
        except httpx.RequestError as e:
            logger.error(f"Request failed: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
            raise HTTPException(status_code=502, detail=f"Proxy request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            if use_unix_socket:
                self.consecutive_socket_failures += 1
            raise HTTPException(status_code=500, detail=f"Internal proxy error: {str(e)}")

    async def proxy_to_host_service(
        self,
        path: str,
        request: Request
    ):
        """
        Proxy requests to host attestation service via Unix socket
        /server/quote -> GET /quote to localhost service
        """
        # Get request details
        method = request.method
        body = await request.body()
        params = dict(request.query_params)
        
        # Extract client certificate info and add to headers
        headers = self.extract_client_cert_info(request)
        
        # Add original request headers
        for key, value in request.headers.items():
            if key.lower() not in ["host", "content-length"]:
                headers[key] = value
        
        # Proxy to host service via Unix socket
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
        request: Request
    ):
        """
        Proxy requests to K8s workload services
        /service/chute-abcd-123/_get_devices -> GET /_get_devices to chute-abcd-123.chutes.svc.cluster.local
        """
        # Validate workload name (basic security)
        if not service_name.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="Invalid service name")
        
        # Get request details
        method = request.method
        body = await request.body()
        params = dict(request.query_params)
        
        # Extract client certificate info and add to headers
        headers = self.extract_client_cert_info(request)
        
        # Add original request headers
        for key, value in request.headers.items():
            if key.lower() not in ["host", "content-length"]:
                headers[key] = value
        
        # Build K8s service URL
        service_url = f"http://{service_name}.{SERVICE_NAMESPACE}.{CLUSTER_DOMAIN}"
        
        # Proxy to K8s workload service
        return await self.proxy_request(
            target_url=service_url,
            method=method,
            path=f"/{path}",
            headers=headers,
            body=body,
            params=params,
            use_unix_socket=False
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
    
def run():
    """Main entry point."""
    try:
        # Load configuration using Pydantic
        config = AttestationProxyConfig()

        # Setup logging level based on config
        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")

        # Validate required TLS configuration
        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        # Create and run server
        server = AttestationProxyServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start Attestation service: %s", e)
        raise


if __name__ == "__main__":
    run()