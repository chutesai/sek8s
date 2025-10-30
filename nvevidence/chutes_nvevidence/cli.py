import json
import sys
import typer

from loguru import logger
from chutes_nvevidence.attestation import NvClient
from chutes_nvevidence.exceptions import NonceError
from chutes_nvevidence.util import validate_nonce

app = typer.Typer(no_args_is_help=True)


def gather_nv_evidence(
    name: str = typer.Option(..., help="Name of the node"),
    nonce: str = typer.Option(..., help="The nonce to include in the evidence"),
):
    try:
        client = NvClient()
        validate_nonce(nonce)
        evidence = client.gather_evidence(name, nonce)

        # Check if evidence is empty or invalid
        if not evidence or (isinstance(evidence, list) and len(evidence) == 0):
            logger.error("Failed to gather GPU evidence: No evidence returned")
            sys.exit(1)

        print(json.dumps(evidence))
        sys.exit(0)
    except NonceError as e:
        logger.error(F"Invalid nonce: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to gather GPU evidence:\n{e}")
        sys.exit(1)


app.command(name="gather-evidence", help="Gather Nvidia GPU evidence.")(gather_nv_evidence)

if __name__ == "__main__":
    app()
