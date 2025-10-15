from asyncio import subprocess
import asyncio

from loguru import logger

from sek8s.exceptions import NvTrustException


class NvEvidenceProvider:
    """Async web server for admission webhook."""

    async def get_evidence(self, name: str, nonce: str) -> str:
        try:
            result = await asyncio.create_subprocess_exec(
                *["chutes-nvevidence", "gather-evidence", "--name", name, "--nonce", nonce],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await result.wait()

            if result.returncode == 0:
                result_output = await result.stdout.read()
                logger.info(f"Successfully generated NVTrust evidence.\n{result_output.decode()}")
                return result_output
            else:
                result_output = await result.stderr.read()
                logger.error(f"Failed to gather GPU evidence:{result_output}")
                raise NvTrustException(f"Failed to gather evidence.")
        except Exception as e:
            logger.error(f"Unexpected error gathering GPU evidence:{e}")
            raise NvTrustException(f"Unexpected error gathering GPU evidence.")
