import asyncio
import base64
from fastapi import HTTPException, Query, status
import logging
from loguru import logger
from sek8s.config import AttestationServiceConfig
from sek8s.exceptions import AttestationException
from sek8s.providers.nvtrust import NvEvidenceProvider
from sek8s.providers.tdx import TdxQuoteProvider
from sek8s.responses import AttestationResponse
from sek8s.server import WebServer

class AttestationServer(WebServer):
    """Async web server for admission webhook."""

    def _setup_routes(self):
        """Setup web routes."""
        self.app.add_api_route("/attest", self.attest, methods=["GET"])
        self.app.add_api_route("/tdx/quote", self.get_quote, methods=["GET"])
        self.app.add_api_route("/nvtrust/evidence", self.get_evidence, methods=["GET"])

    async def attest(self, nonce: str = Query(..., description="Nonce to include in the quote")):
        try:
            tdx_provider = TdxQuoteProvider()
            nvtrust_provider = NvEvidenceProvider()

            quote_content = await tdx_provider.get_quote(nonce)
            nvtrust_evidence = await nvtrust_provider.get_evidence(self.config.host, nonce)

            return AttestationResponse(
                tdx_quote=base64.b64encode(quote_content).decode('utf-8'),
                nvtrust_evidence = nvtrust_evidence
            )

        except AttestationException as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected exception encountered generating attestaion data: {e}"
            )

    async def get_quote(self, nonce: str = Query(..., description="Nonce to include in the quote")):
        try:
            provider = TdxQuoteProvider()
            quote_content = await provider.get_quote(nonce)
            
            return base64.b64encode(quote_content).decode('utf-8')
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating TDX quote:{e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error generating TDX quote.",
            )

    async def get_evidence(
        name: str = Query(
            None, description="Name of the node to include in the evidence"
        ),
        nonce: str = Query(
            None, description="Nonce to include in the evidence"
        )
    ):
        try:
            provider = NvEvidenceProvider()
            evidence = await provider.get_evidence(name, nonce)

            return evidence
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
        config = AttestationServiceConfig()

        # Setup logging level based on config
        if config.debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")
            logger.debug("Configuration: %s", config.export_json())

        # Validate required TLS configuration
        if not config.tls_cert_path or not config.tls_key_path:
            logger.warning("TLS certificates not configured, running in insecure mode")

        # Create and run server
        server = AttestationServer(config)
        server.run()

    except Exception as e:
        logger.exception("Failed to start Attestation service: %s", e)
        raise


if __name__ == "__main__":
    run()
