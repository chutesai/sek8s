from sek8s.config import SystemStatusConfig


def test_blank_tls_paths_resolve_to_none(monkeypatch):
    monkeypatch.setenv("TLS_CERT_PATH", "")
    monkeypatch.setenv("TLS_KEY_PATH", "")
    monkeypatch.setenv("CLIENT_CA_PATH", "")

    config = SystemStatusConfig()

    assert config.tls_cert_path is None
    assert config.tls_key_path is None
    assert config.client_ca_path is None
