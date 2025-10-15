import asyncio
import base64
import tempfile

from loguru import logger

from sek8s.exceptions import TdxQuoteException


QUOTE_GENERATOR_BINARY = "/usr/bin/tdx-quote-generator"


class TdxQuoteProvider():
    """Async web server for admission webhook."""

    async def get_quote(self, nonce: str) -> bytes:
        try:
            with tempfile.NamedTemporaryFile(mode="rb", suffix=".bin") as fp:
                result = await asyncio.create_subprocess_exec(
                    *[QUOTE_GENERATOR_BINARY, "--user-data", nonce, "--output", fp.name],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                await result.wait()

                if result.returncode == 0:
                    # Return base64 encoded content of file
                    result_output = await result.stdout.read()
                    logger.info(f"Successfully generated quote.\n{result_output.decode()}")
                    
                    # Read the quote from the file
                    fp.seek(0)
                    quote_content = fp.read()

                    return quote_content
                else:
                    result_output = await result.stderr.read()
                    logger.error(f"Failed to generate quote: {result_output.decode()}")
                    raise TdxQuoteException(f"Failed to generate quote.")
        except Exception as e:
            logger.error(f"Unexpected error generating TDX quote:{e}")
            raise TdxQuoteException(f"Unexpected error generating TDX quote.")