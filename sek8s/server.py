
from abc import abstractmethod
from aiohttp import web
from loguru import logger

from sek8s.config import ServerConfig


class WebServer:
    """Async web server for admission webhook."""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.app = web.Application()
        self._setup_routes()
    
    @abstractmethod
    def _setup_routes(self):
        """
        Setup web routes.
        self.app.router.add_post('/[route]', self.handle_[route])
        """
        raise NotImplementedError()
    
    def run(self):
        """Run the webhook server."""
        logger.info("Starting admission webhook server on %s:%d",
                   self.config.bind_address, self.config.port)
        
        # Setup SSL if configured
        ssl_context = None
        if self.config.tls_cert_path and self.config.tls_key_path:
            import ssl
            ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_context.load_cert_chain(
                self.config.tls_cert_path,
                self.config.tls_key_path
            )
            logger.info("TLS enabled")
        
        web.run_app(
            self.app,
            host=self.config.bind_address,
            port=self.config.port,
            ssl_context=ssl_context,
            access_log=logger if self.config.debug else None
        )