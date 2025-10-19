from abc import abstractmethod
import os
from typing import Callable, Optional
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from loguru import logger
import uvicorn
import ssl

from starlette.types import Lifespan
from fastapi.applications import AppType
from sek8s.config import ServerConfig

class WebServer:
    """Async web server for admission webhook using FastAPI."""

    def __init__(self, config: ServerConfig, lifespan: Optional[Lifespan[AppType]] = None):
        self.config = config
        self.app = FastAPI(
            debug=config.debug,
            default_response_class=ORJSONResponse, 
            lifespan=lifespan
        )
        self._setup_routes()

    @abstractmethod
    def _setup_routes(self):
        """
        Setup web routes.
        Example: 
        self.app.add_api_route('/route', self.handle_route, methods=["GET"])
        """
        raise NotImplementedError()

    def run(self):
        """Run the webhook server."""
        # Build kwargs dynamically for uvicorn.run
        uvicorn_kwargs = {}

        if self.config.uds_path:
            logger.info(f"Starting admission webhook server on Unix socket {self.config.uds_path}")
            uvicorn_kwargs["uds"] = self.config.uds_path
        else:
            logger.info(
                f"Starting admission webhook server on {self.config.bind_address}:{self.config.port}"
            )
            uvicorn_kwargs["host"] = self.config.bind_address
            uvicorn_kwargs["port"] = self.config.port
            
            # TLS is always required for TCP connections
            if not self.config.tls_cert_path or not self.config.tls_key_path:
                raise ValueError("TLS certificate and key are required for TCP connections")
            
            uvicorn_kwargs["ssl_certfile"] = self.config.tls_cert_path
            uvicorn_kwargs["ssl_keyfile"] = self.config.tls_key_path
            logger.info("TLS enabled")

            # Configure mTLS if required
            if self.config.mtls_required:
                if not self.config.client_ca_path or not os.path.exists(self.config.client_ca_path):
                    raise ValueError(f"mTLS requires valid client CA certificate: {self.config.client_ca_path}")
                
                uvicorn_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
                uvicorn_kwargs["ssl_ca_certs"] = self.config.client_ca_path
                logger.info(f"mTLS enabled with CA: {self.config.client_ca_path}")
            else:
                logger.info("mTLS disabled - no client certificate verification")

        uvicorn.run(
            self.app,
            log_level="debug" if self.config.debug else "info",
            **uvicorn_kwargs
        )