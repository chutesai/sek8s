import json
import sys
import typer

from loguru import logger
from chutes_nvevidence.attestation import NvClient

app = typer.Typer(no_args_is_help=True)


def gather_nv_evidence(
    name: str = typer.Option(..., help="Name of the node"),
    nonce: str = typer.Option(..., help="The nonce to include in the evidence"),
):
    try:
        client = NvClient()
        evidence = client.gather_evidence(name, nonce)

        print(json.dumps(evidence))
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to gather GPU evidence:\n{e}")
        sys.exit(1)


app.command(name="gather-evidence", help="Gather Nvidia GPU evidence.")(gather_nv_evidence)

if __name__ == "__main__":
    app()
