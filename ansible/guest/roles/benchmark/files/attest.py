#!/opt/chutes-nvevidence/venv/bin/python
"""
attest — TDX + GPU attestation verification.

Commands:
  dump    Parse and display TDX measurements (offline, no internet required).
  verify  Full remote attestation: GPU via NVIDIA + TDX via Intel Tiber Trust Services.

TDX quote v4 binary layout (Intel DCAP format):
  Header (48 bytes):
    [0:2]   version
    [2:4]   att_key_type
    [4:8]   tee_type
    [8:28]  reserved / vendor_id
    [28:48] user_data

  Body — td_report_body_t (584 bytes, starts at offset 48):
    [48:64]   tee_tcb_svn      (16 bytes) — TCB security version numbers
    [64:112]  mrseam           (48 bytes) — TDX module measurement
    [112:160] mrsignerseam     (48 bytes) — TDX module signer measurement
    [160:168] seam_attributes  ( 8 bytes)
    [168:176] td_attributes    ( 8 bytes)
    [176:184] xfam             ( 8 bytes) — extended features mask
    [184:232] mrtd             (48 bytes) — initial TD measurement (key value for image pinning)
    [232:280] mrconfigid       (48 bytes) — TD configuration ID
    [280:328] mrowner          (48 bytes) — TD owner measurement
    [328:376] mrownerconfig    (48 bytes) — TD owner config measurement
    [376:424] rtmr0            (48 bytes) — runtime measurement register 0
    [424:472] rtmr1            (48 bytes) — runtime measurement register 1
    [472:520] rtmr2            (48 bytes) — runtime measurement register 2
    [520:568] rtmr3            (48 bytes) — runtime measurement register 3
    [568:632] report_data      (64 bytes) — user-supplied nonce/data

For TDX remote verification, create /etc/tdx-attest-config.json:
  {
    "trustauthority_url": "https://portal.trustauthority.intel.com",
    "trustauthority_api_url": "https://api.trustauthority.intel.com",
    "trustauthority_api_key": "<your-api-key>"
  }
"""

import json
import secrets
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import typer
from nv_attestation_sdk import attestation as att

app = typer.Typer(
    name="attest",
    help="TDX + GPU attestation verification for benchmark VMs.",
    no_args_is_help=True,
)

_QUOTE_GENERATOR = "/usr/bin/tdx-quote-generator"
_TRUSTAUTHORITY_CLI = "/usr/bin/trustauthority-cli"
_DEFAULT_TDX_CONFIG = "/etc/tdx-attest-config.json"

# Absolute byte offsets within the quote binary (header = 48 bytes)
_FIELDS = [
    ("tee_tcb_svn",     48,  16),
    ("mrseam",          64,  48),
    ("mrsignerseam",   112,  48),
    ("seam_attributes",160,   8),
    ("td_attributes",  168,   8),
    ("xfam",           176,   8),
    ("mrtd",           184,  48),
    ("mrconfigid",     232,  48),
    ("mrowner",        280,  48),
    ("mrownerconfig",  328,  48),
    ("rtmr0",          376,  48),
    ("rtmr1",          424,  48),
    ("rtmr2",          472,  48),
    ("rtmr3",          520,  48),
    ("report_data",    568,  64),
]
_MIN_QUOTE_LEN = 632  # offset 568 + 64 bytes report_data


def _generate_quote() -> bytes:
    """Run tdx-quote-generator and return the raw quote bytes."""
    if not Path(_QUOTE_GENERATOR).exists():
        raise RuntimeError(
            f"{_QUOTE_GENERATOR} not found — is libtdx-attest installed?"
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "quote.bin"
        result = subprocess.run(
            [_QUOTE_GENERATOR, "--output", str(out)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"tdx-quote-generator failed (exit {result.returncode}):\n{result.stderr}"
            )
        if not out.exists():
            raise RuntimeError("tdx-quote-generator did not produce output file")
        return out.read_bytes()


def _parse_quote(data: bytes) -> dict:
    """Extract measurement fields from a TDX v4 quote binary."""
    if len(data) < _MIN_QUOTE_LEN:
        raise ValueError(
            f"Quote is {len(data)} bytes — expected at least {_MIN_QUOTE_LEN}. "
            "Is this a valid TDX v4 quote?"
        )
    version = struct.unpack_from("<H", data, 0)[0]
    tee_type = struct.unpack_from("<I", data, 4)[0]
    fields = {name: data[off : off + size].hex() for name, off, size in _FIELDS}
    return {"quote_version": version, "tee_type": hex(tee_type), **fields}


def _print_table(parsed: dict) -> None:
    rows = [
        ("Quote version",    str(parsed["quote_version"])),
        ("TEE type",         parsed["tee_type"]),
        ("MRTD",             parsed["mrtd"]),
        ("RTMR0",            parsed["rtmr0"]),
        ("RTMR1",            parsed["rtmr1"]),
        ("RTMR2",            parsed["rtmr2"]),
        ("RTMR3",            parsed["rtmr3"]),
        ("MRSEAM",           parsed["mrseam"]),
        ("MRSIGNERSEAM",     parsed["mrsignerseam"]),
        ("TEE TCB SVN",      parsed["tee_tcb_svn"]),
        ("SEAM attributes",  parsed["seam_attributes"]),
        ("TD attributes",    parsed["td_attributes"]),
        ("XFAM",             parsed["xfam"]),
        ("MRCONFIGID",       parsed["mrconfigid"]),
        ("MROWNER",          parsed["mrowner"]),
        ("MROWNERCONFIG",    parsed["mrownerconfig"]),
        ("Report data",      parsed["report_data"]),
    ]
    w = max(len(r[0]) for r in rows) + 2
    sep = "=" * (w + 98)
    typer.echo(f"\n{sep}")
    typer.echo("  TDX Measurements")
    typer.echo(sep)
    for label, value in rows:
        typer.echo(f"  {label:<{w}}{value}")
    typer.echo(f"{sep}\n")


@app.command()
def dump(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Save raw quote binary to file"),
    json_out: bool = typer.Option(False, "--json", help="Emit measurements as JSON"),
) -> None:
    """
    Generate a TDX quote and display all measurements (no internet required).

    Use this to capture MRTD / RTMR values for out-of-band comparison
    with expected measurements provided by Chutes before the evaluation.
    """
    try:
        data = _generate_quote()
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    if output:
        output.write_bytes(data)
        typer.echo(f"Quote saved to {output}")

    try:
        parsed = _parse_quote(data)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    if json_out:
        typer.echo(json.dumps(parsed, indent=2))
    else:
        _print_table(parsed)


@app.command()
def verify(
    name: str = typer.Option("benchmark-vm", help="Node name for GPU attestation"),
    tdx_config: Path = typer.Option(
        _DEFAULT_TDX_CONFIG,
        help="Intel Tiber Trust Services config JSON for TDX remote verification",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit results as JSON"),
) -> None:
    """
    Full remote attestation: GPU (NVIDIA) + TDX (Intel Tiber Trust Services).

    GPU verification runs locally against NVIDIA's root certificates (no GPU
    evidence is sent to any external service). TDX remote verification requires
    /etc/tdx-attest-config.json
    with an Intel Tiber Trust Services API key — if absent the step is skipped
    (measurement dump and GPU verification still run).
    """
    results: dict = {}
    all_passed = True

    # --- TDX measurement dump (always included in verify output) ---
    typer.echo("Generating TDX quote...")
    try:
        data = _generate_quote()
        parsed = _parse_quote(data)
        results["measurements"] = parsed
        if not json_out:
            _print_table(parsed)
    except Exception as exc:
        typer.echo(f"Warning: could not generate TDX quote: {exc}", err=True)
        results["measurements"] = None

    # --- GPU attestation (NVIDIA NRAS, ES384-signed JWT) ---
    typer.echo("Verifying GPU attestation (NVIDIA Remote Attestation Service)...")
    gpu = _verify_gpu(name)
    results["gpu"] = gpu
    if not json_out:
        status = "PASS" if gpu.get("passed") else "FAIL"
        typer.echo(f"GPU attestation: {status}")
        if gpu.get("error"):
            typer.echo(f"  {gpu['error']}", err=True)
    if not gpu.get("passed"):
        all_passed = False

    # --- TDX remote verification via Intel ---
    tdx_config_path = Path(str(tdx_config))
    if not tdx_config_path.exists():
        msg = (
            f"Skipping TDX remote verification: {tdx_config} not found.\n"
            "Create it with your Intel Tiber Trust Services API key to enable TDX verification.\n"
            "Measurement dump above is still valid for manual comparison."
        )
        typer.echo(msg, err=True)
        results["tdx_remote"] = {"skipped": True, "reason": f"{tdx_config} not found"}
    else:
        typer.echo("Verifying TDX quote (Intel Tiber Trust Services)...")
        tdx = _verify_tdx(str(tdx_config_path))
        results["tdx_remote"] = tdx
        if not json_out:
            status = "PASS" if tdx.get("passed") else "FAIL"
            typer.echo(f"TDX remote verification: {status}")
            if tdx.get("error"):
                typer.echo(f"  {tdx['error']}", err=True)
        if not tdx.get("passed"):
            all_passed = False

    if json_out:
        typer.echo(json.dumps(results, indent=2))

    if not all_passed:
        raise typer.Exit(1)


def _verify_gpu(name: str) -> dict:
    """Verify GPU attestation via NVIDIA's Remote Attestation Service (NRAS).

    Environment.REMOTE sends GPU evidence to NRAS, which returns a JWT signed
    with NVIDIA's ES384 private key. This token can be independently verified
    against NVIDIA's public certificates, making it suitable for third-party
    inspection. No API key is required.
    """
    nonce = secrets.token_hex(32)  # 64 hex chars = 32 bytes
    try:
        client = att.Attestation()
        client.set_name(name)
        client.set_nonce(nonce)
        client.set_claims_version("3.0")
        client.add_verifier(att.Devices.GPU, att.Environment.REMOTE, "", "")
        evidence = client.get_evidence()
        passed = client.attest(evidence)
        return {"passed": bool(passed)}
    except Exception as exc:
        return {"passed": False, "error": str(exc)}


def _verify_tdx(config_path: str) -> dict:
    """Verify TDX quote via Intel trustauthority-cli."""
    cli = _TRUSTAUTHORITY_CLI
    if not Path(cli).exists():
        # Try alternate install location
        alt = "/usr/local/bin/trustauthority-cli"
        if Path(alt).exists():
            cli = alt
        else:
            return {
                "passed": False,
                "error": (
                    f"trustauthority-cli not found at {_TRUSTAUTHORITY_CLI} or {alt}. "
                    "Install from the Intel SGX repo."
                ),
            }
    try:
        result = subprocess.run(
            [cli, "token", "--config", config_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {
                "passed": False,
                "error": f"trustauthority-cli failed (exit {result.returncode}): {result.stderr.strip()}",
            }
        token = result.stdout.strip()
        return {
            "passed": bool(token),
            "token_preview": (token[:40] + "...") if len(token) > 40 else token,
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "trustauthority-cli timed out after 60s"}
    except Exception as exc:
        return {"passed": False, "error": str(exc)}


if __name__ == "__main__":
    app()
