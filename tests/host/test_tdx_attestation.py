"""Host-side TDX attestation service configuration (QGS, QCNL, PCCS)."""

from unittest.mock import call, patch

import pytest
from chutes_cvm.host import system, tdx_attestation

_QGS_CONF = "# QGS config\n#port = 4050\nnumber_threads = 4\n"
_QCNL_CONF = '{\n  // local PCCS\n  "pccs_url": "https://localhost:8081",\n  "use_secure_cert": true,\n}\n'


@pytest.fixture
def mock_write():
    with patch("chutes_cvm.host.tdx_attestation.write_system_file") as m:
        yield m


@pytest.fixture
def mock_run():
    with patch("chutes_cvm.host.tdx_attestation.run") as m:
        yield m


def test_qgs_is_switched_to_vsock_and_restarted(tmp_path, mock_write, mock_run):
    conf = tmp_path / "qgs.conf"
    conf.write_text(_QGS_CONF)
    tdx_attestation._configure_qgs_vsock(str(conf))

    path, content = mock_write.call_args.args
    assert path == str(conf)
    assert "\nport = 4050\n" in content
    assert "number_threads = 4" in content
    mock_run.assert_called_once_with(["sudo", "systemctl", "restart", "qgsd"])


def test_qgs_already_on_vsock_is_left_alone(tmp_path, mock_write, mock_run):
    conf = tmp_path / "qgs.conf"
    conf.write_text("port = 4050\n")
    tdx_attestation._configure_qgs_vsock(str(conf))
    mock_write.assert_not_called()
    mock_run.assert_not_called()


def test_qcnl_accepts_the_local_pccs_cert_keeping_comments(tmp_path, mock_write):
    conf = tmp_path / "qcnl.conf"
    conf.write_text(_QCNL_CONF)
    tdx_attestation._configure_qcnl(str(conf))

    _, content = mock_write.call_args.args
    assert '"use_secure_cert": false' in content
    assert "// local PCCS" in content


def test_qcnl_already_patched_is_left_alone(tmp_path, mock_write):
    conf = tmp_path / "qcnl.conf"
    conf.write_text('{"use_secure_cert": false}')
    tdx_attestation._configure_qcnl(str(conf))
    mock_write.assert_not_called()


def test_missing_configs_are_skipped(tmp_path, mock_write, mock_run):
    tdx_attestation._configure_qgs_vsock(str(tmp_path / "absent"))
    tdx_attestation._configure_qcnl(str(tmp_path / "absent"))
    tdx_attestation._ensure_pccs_node_modules(str(tmp_path / "absent"))
    mock_write.assert_not_called()
    mock_run.assert_not_called()


def test_pccs_modules_are_installed_only_when_missing(tmp_path, mock_run):
    (tmp_path / "package.json").write_text("{}")
    tdx_attestation._ensure_pccs_node_modules(str(tmp_path))
    mock_run.assert_called_once_with(
        ["npm", "install", "--prefer-offline"], cwd=str(tmp_path)
    )

    mock_run.reset_mock()
    (tmp_path / "node_modules").mkdir()
    tdx_attestation._ensure_pccs_node_modules(str(tmp_path))
    mock_run.assert_not_called()


@pytest.mark.parametrize("noninteractive", [True, False])
def test_configure_runs_pccs_npm_only_noninteractive(noninteractive):
    with patch.object(
        tdx_attestation, "_ensure_pccs_node_modules"
    ) as pccs, patch.object(
        tdx_attestation, "_configure_qgs_vsock"
    ) as qgs, patch.object(
        tdx_attestation, "_configure_qcnl"
    ) as qcnl:
        tdx_attestation.configure(noninteractive)
    assert pccs.called is noninteractive
    qgs.assert_called_once_with()
    qcnl.assert_called_once_with()


def test_write_system_file_pipes_content_through_sudo_tee():
    with patch("chutes_cvm.host.system.proc.run") as proc_run:
        system.write_system_file("/etc/x.conf", "a=1\n")
    assert proc_run.call_args == call(
        ["sudo", "tee", "/etc/x.conf"],
        check=True,
        input=b"a=1\n",
        stdout=system.proc.DEVNULL,
    )
