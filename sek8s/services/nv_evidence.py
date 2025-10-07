
from asyncio import subprocess
import json
from fastapi import HTTPException, Query, status
import logging
from loguru import logger
from sek8s.config import NvEvidenceServiceConfig, ServerConfig
from sek8s.server import WebServer

class NvEvidenceServer(WebServer):
    """Async web server for admission webhook."""
    
    def _setup_routes(self):
        """Setup web routes."""
        self.app.router.get('/evidence', self.get_evidence)

    def get_evidence(
            name: str = Query(
                None, description="Name of the node to include in the evidence"
            ),
            nonce: str = Query(
                None, description="Nonce to include in the evidence"
            )
    ):
        try:
            result = subprocess.run(
                ["chutes-nvevidence", "gather-evidence", "--name", name, "--nonce", nonce],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                return 
                data = json.loads(result.stdout)
                print(f"Success: {data}")
            else:
                logger.error(f"Failed to gather GPU evidence:{result.stdout}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to gather evidence."
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error gathering GPU evidence:{e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error gathering GPU evidence."
            )

def run():
    """Main entry point."""
    try:
        # Load configuration using Pydantic
        config = NvEvidenceServiceConfig()
        
        # Setup logging level based on config
        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())
        
        # Validate required TLS configuration
        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")
        
        # Create and run server
        server = NvEvidenceServer(config)
        server.run()
        
    except Exception as e:
        logger.exception("Failed to start admission controller: %s", e)
        raise


if __name__ == "__main__":
    run()