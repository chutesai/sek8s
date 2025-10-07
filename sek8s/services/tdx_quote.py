import asyncio
import tempfile
from fastapi import HTTPException, Query, status
import logging
from loguru import logger
from sek8s.config import ServerConfig
from sek8s.server import WebServer

QUOTE_GENERATOR_BINARY = "/usr/bin/tdx-quote-generator"


class TdxQuoteServer(WebServer):
    """Async web server for admission webhook."""

    def _setup_routes(self):
        """Setup web routes."""
        self.app.router.get("/quote", self.get_quote)

    async def get_quote(self, nonce: str = Query(..., description="Nonce to include in the quote")):
        try:
            with tempfile.NamedTemporaryFile(mode="r", suffix=".bin") as fp:
                result = await asyncio.create_subprocess_exec(
                    *[QUOTE_GENERATOR_BINARY, "--user-data", nonce, "--output", fp.name],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                await result.wait()

                if result.returncode == 0:
                    # Return base64 encoded content of file
                    logger.info("Successfully generated quote.")
                    logger.info(result.stdout.read())
                    return
                else:
                    logger.error(f"Failed to generate quote:{result.stderr.read()}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to generate quote.",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating TDX quote:{e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error generating TDX quote.",
            )


def run():
    """Main entry point."""
    try:
        # Load configuration using Pydantic
        config = ServerConfig()

        # Setup logging level based on config
        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())

        # Validate required TLS configuration
        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        # Create and run server
        server = TdxQuoteServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start TDX Quote service: %s", e)
        raise


if __name__ == "__main__":
    run()
