"""Shell helpers for host setup: run a root-level command, write a root-owned file."""

from chutes_cvm import proc


def run(cmd: list[str], **kwargs):
    """Run a command, printing it first. Raises on failure."""
    print(f"  $ {' '.join(cmd)}")
    proc.run(cmd, check=True, **kwargs)


def write_system_file(path: str, content: str):
    """Write content to a root-owned system file via tee."""
    run(
        ["sudo", "tee", path],
        input=content.encode(),
        stdout=proc.DEVNULL,
    )
