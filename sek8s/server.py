from abc import abstractmethod
from fastapi import FastAPI
from loguru import logger
import uvicorn
import ssl

from sek8s.config import ServerConfig

class WebServer:
    """Async web server for admission webhook using FastAPI."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.app = FastAPI(debug=config.debug)
        self._setup_routes()

    @abstractmethod
    def _setup_routes(self):
        """
        Setup web routes.
        Example: 
        @self.app.get('/route')
        async def handle_route():
            pass
        """
        raise NotImplementedError()

    def run(self):
        """Run the webhook server."""
        logger.info(
            f"Starting admission webhook server on {self.config.bind_address}:{self.config.port}"
        )

        # Setup SSL if configured
        ssl_certfile = None
        ssl_keyfile = None
        if self.config.tls_cert_path and self.config.tls_key_path:
            ssl_certfile = self.config.tls_cert_path
            ssl_keyfile = self.config.tls_key_path
            logger.info("TLS enabled")

        # Run the FastAPI app with uvicorn
        uvicorn.run(
            self.app,
            host=self.config.bind_address,
            port=self.config.port,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            log_level="debug" if self.config.debug else "info"
        )