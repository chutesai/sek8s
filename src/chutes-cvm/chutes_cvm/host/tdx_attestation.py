"""Host-side TDX attestation services: PCCS, QGS and the QCNL client that connects them.

A TDX guest's quote is signed on the host by QGS (over vsock), which fetches collateral from the
local PCCS cache. The packages come from the TDX host recipe; this configures them.
"""

import os
import re

from chutes_cvm.host.system import run, write_system_file


def configure(noninteractive: bool) -> None:
    """TDX quotes are produced on the host: QGS signs them, fed by the local PCCS cache."""
    if noninteractive:
        # sgx-dcap-pccs post-install only runs npm install during interactive
        # debconf prompts; in non-interactive mode we must do it ourselves.
        _ensure_pccs_node_modules()
    print("  QGS: vsock (port 4050)...")
    _configure_qgs_vsock()
    print("  QCNL: accept the local PCCS self-signed cert...")
    _configure_qcnl()


def _configure_qgs_vsock(conf_path: str = "/etc/qgs.conf"):
    """Ensure QGS uses vsock (port 4050) rather than a Unix domain socket.

    The default shipped config has the port line commented out, which causes
    QGS to bind a Unix socket that the VM cannot reach.  We uncomment/set
    'port = 4050' so QGS listens on vsock and restarts the service if the
    file was changed.
    """
    if not os.path.exists(conf_path):
        print(f"  {conf_path} not found — QGS not installed yet, skipping")
        return

    with open(conf_path) as f:
        original = f.read()

    updated = re.sub(r"^#?\s*port\s*=.*$", "port = 4050", original, flags=re.MULTILINE)

    if updated == original:
        print(f"  {conf_path} already set to vsock port 4050")
        return

    print(f"  Configuring {conf_path}: enabling vsock port 4050")
    write_system_file(conf_path, updated)
    run(["sudo", "systemctl", "restart", "qgsd"])


def _configure_qcnl(conf_path: str = "/etc/sgx_default_qcnl.conf"):
    """Ensure QCNL accepts PCCS's self-signed TLS certificate.

    PCCS runs locally with a self-signed cert.  The default QCNL config ships
    with use_secure_cert=true which causes CURL error 60 on every quote
    request.  We patch it to false so QGS can reach the local PCCS.

    The file is a JSON5-ish format (allows comments and trailing commas) so
    we use a regex patch rather than json.loads to avoid stripping comments.
    """
    if not os.path.exists(conf_path):
        print(f"  {conf_path} not found — QCNL not installed yet, skipping")
        return

    with open(conf_path) as f:
        original = f.read()

    updated = re.sub(
        r'"use_secure_cert"\s*:\s*true',
        '"use_secure_cert": false',
        original,
    )

    if updated == original:
        print(f"  {conf_path} already has use_secure_cert=false")
        return

    print(f"  Patching {conf_path}: use_secure_cert → false")
    write_system_file(conf_path, updated)


def _ensure_pccs_node_modules(pccs_dir: str = "/opt/intel/sgx-dcap-pccs"):
    """Run npm install in the PCCS directory when node_modules are absent.

    sgx-dcap-pccs Debian post-install only calls npm install during interactive
    debconf prompts.  With DEBIAN_FRONTEND=noninteractive the step is skipped,
    leaving node_modules/ empty and the service unable to start.
    """
    node_modules = os.path.join(pccs_dir, "node_modules")
    if os.path.isdir(node_modules):
        print(f"  {pccs_dir}/node_modules already present, skipping npm install")
        return

    package_json = os.path.join(pccs_dir, "package.json")
    if not os.path.exists(package_json):
        print(
            f"  {pccs_dir}/package.json not found — sgx-dcap-pccs not installed, skipping"
        )
        return

    print(f"  node_modules missing in {pccs_dir}, running npm install...")
    run(["npm", "install", "--prefer-offline"], cwd=pccs_dir)
    print("  ✓ PCCS node_modules installed")
