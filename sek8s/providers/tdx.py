import asyncio
import base64
import tempfile
import hashlib
import subprocess

from loguru import logger

from sek8s.exceptions import TdxQuoteException


QUOTE_GENERATOR_BINARY = "/usr/bin/tdx-quote-generator"
SERVER_CERT = "/etc/attestation-service/certs/server.crt"

class TdxQuoteProvider():
    """Async TDX quote provider with cert hash binding."""

    def _get_cert_hash(self) -> str:
        """
        Compute SHA-256 hash of the server certificate's public key.
        This binds the quote to the specific certificate being used.
        
        Returns:
            64-character hex string (SHA-256 hash)
        """
        try:
            # Extract public key from certificate
            pubkey_result = subprocess.run(
                ["openssl", "x509", "-in", SERVER_CERT, "-pubkey", "-noout"],
                capture_output=True,
                check=True,
                text=True  # Get string output
            )
            
            # Convert public key to DER format and hash it
            der_result = subprocess.run(
                ["openssl", "pkey", "-pubin", "-outform", "der"],
                input=pubkey_result.stdout.encode('utf-8'),  # Encode string to bytes
                capture_output=True,
                check=True,
                text=False  # Keep as False since we need bytes output
            )
            
            # Compute SHA-256 hash
            cert_hash = hashlib.sha256(der_result.stdout).hexdigest()
            
            logger.debug(f"Computed cert hash: {cert_hash}")
            return cert_hash
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to compute cert hash: {e}")
            raise TdxQuoteException(f"Failed to compute certificate hash: {e}")
        except Exception as e:
            logger.error(f"Unexpected error computing cert hash: {e}")
            raise TdxQuoteException(f"Unexpected error computing certificate hash: {e}")

    async def get_quote(self, nonce: str) -> bytes:
        """
        Generate a TDX quote with nonce and certificate hash in report data.
        
        Args:
            nonce: 64-character hex string (32 bytes)
            
        Returns:
            Raw quote bytes
        """
        try:
            # Get certificate hash
            cert_hash = self._get_cert_hash()
            
            # Combine nonce and cert hash for report data
            # TDX report data is 64 bytes (128 hex chars)
            # We have: 64 chars (nonce) + 64 chars (cert_hash) = 128 chars
            report_data = f"{nonce}{cert_hash}"
            
            # Truncate to 128 hex chars (64 bytes) if needed
            report_data = report_data[:128]
            
            logger.debug(f"Report data: nonce({len(nonce)}) + cert_hash({len(cert_hash)}) = {len(report_data)} chars")
            
            with tempfile.NamedTemporaryFile(mode="rb", suffix=".bin") as fp:
                result = await asyncio.create_subprocess_exec(
                    *[QUOTE_GENERATOR_BINARY, "--report-data", report_data, "--hex", "--output", fp.name],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                await result.wait()

                if result.returncode == 0:
                    result_output = await result.stdout.read()
                    logger.info(f"Successfully generated quote with nonce and cert hash.\n{result_output.decode()}")
                    
                    # Read the quote from the file
                    fp.seek(0)
                    quote_content = fp.read()

                    return quote_content
                else:
                    result_output = await result.stderr.read()
                    logger.error(f"Failed to generate quote: {result_output.decode()}")
                    raise TdxQuoteException(f"Failed to generate quote.")
        except TdxQuoteException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error generating TDX quote: {e}")
            raise TdxQuoteException(f"Unexpected error generating TDX quote: {e}")