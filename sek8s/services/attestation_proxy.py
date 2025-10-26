from contextlib import asynccontextmanager
import logging
from typing import Dict
from urllib.parse import urljoin
from fastapi import FastAPI, HTTPException, Request, Response
from loguru import logger
from sek8s.config import AttestationProxyConfig
from sek8s.server import WebServer
import httpx

# Configuration
HOST_ATTESTATION_URL = "http://unix:/var/run/attestation/attestation.sock"
SERVICE_NAMESPACE = "chutes"
CLUSTER_DOMAIN = "svc.cluster.local"

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize HTTP clients on startup"""
    global unix_client, http_client
    
    # Client for Unix socket communication (host service)
    unix_client = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds="/var/run/attestation/attestation.sock"),
        base_url="http://localhost",
        timeout=httpx.Timeout(30.0)
    )
    
    # Client for K8s service communication (workloads)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        verify=False  # Internal cluster communication
    )
    
    logger.info("Attestation proxy started successfully")

    yield 

    if unix_client:
        await unix_client.aclose()
    if http_client:
        await http_client.aclose()

    logger.info("Attestation proxy shutdown complete")

class AttestationProxyServer(WebServer):
    """Async web server for admission webhook."""

    def __init__(self, config: AttestationProxyConfig):
        super().__init__(config, lifespan=lifespan)

    def _setup_routes(self):
        """Setup web routes."""
        self.app.add_api_route("/health", self.health_check, methods=["GET"])
        self.app.add_exception_handler(404, self.not_found_handler)
        self.app.add_api_route("/server/{path:path}", self.proxy_to_host_service, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        self.app.add_api_route("/service/{service_name}/{path:path}", self.proxy_to_service, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])

    async def health_check(self):
        """Health check endpoint"""
        return {
            "status": "healthy",
            "service": "attestation-proxy",
            "unix_client": unix_client is not None,
            "http_client": http_client is not None
        }
    
    async def not_found_handler(self, request: Request, exc):
        """Custom 404 handler"""
        return Response(
            content=f"Proxy route not found: {request.url.path}",
            status_code=404,
            media_type="text/plain"
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
        Proxy a request to the target service
        """
        client = unix_client if use_unix_socket else http_client
        
        if not client:
            raise HTTPException(status_code=503, detail="HTTP client not initialized")
        
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
            # Make the proxied request
            logger.info(f"Proxying {method} {full_url}")
            
            response = await client.request(
                method=method,
                url=full_url,
                headers=filtered_headers,
                content=body,
                params=params,
                follow_redirects=False
            )
            
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
            
        except httpx.RequestError as e:
            logger.error(f"Request failed: {e}")
            raise HTTPException(status_code=502, detail=f"Proxy request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
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