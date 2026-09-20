import asyncio
import tempfile

from loguru import logger

from sek8s.exceptions import NonceError, TdxQuoteException
from sek8s.providers.base import QuoteProvider

QUOTE_GENERATOR_BINARY = "/usr/bin/tdx-quote-generator"


class TdxQuoteProvider(QuoteProvider):
    """Async TDX quote provider with cert hash binding."""

    tee_type = "tdx"
    guest_device = "/dev/tdx_guest"
    quote_exception = TdxQuoteException

    async def get_quote(self, nonce: str) -> bytes:
        """
        Generate a TDX quote with nonce and certificate hash in report data.

        Args:
            nonce: 64-character hex string (32 bytes)

        Returns:
            Raw quote bytes
        """
        try:
            # nonce ‖ cert_hash, nonce-validated and width-asserted by the base.
            report_data = self._build_report_data(nonce)

            with tempfile.NamedTemporaryFile(mode="rb", suffix=".bin") as fp:
                result = await asyncio.create_subprocess_exec(
                    *[
                        QUOTE_GENERATOR_BINARY,
                        "--report-data",
                        report_data,
                        "--hex",
                        "--output",
                        fp.name,
                    ],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                await result.wait()

                if result.returncode == 0:
                    if result.stdout is None:
                        raise TdxQuoteException("No stdout from quote command")
                    result_output = await result.stdout.read()
                    logger.info(
                        f"Successfully generated quote with nonce and cert hash.\n{result_output.decode()}"
                    )

                    # Read the quote from the file
                    fp.seek(0)
                    quote_content = fp.read()

                    return quote_content
                else:
                    if result.stderr is None:
                        raise TdxQuoteException("No stderr from quote command")
                    result_output = await result.stderr.read()
                    logger.error(f"Failed to generate quote: {result_output.decode()}")
                    raise TdxQuoteException("Failed to generate quote.")
        except (NonceError, TdxQuoteException):
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating TDX quote: {e}")
            raise TdxQuoteException(f"Unexpected error generating TDX quote: {e}")
