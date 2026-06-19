"""Unit tests for the model-download retry classifier and log redaction.

The classifier must retry only transient download-layer failures (CDN presigned-URL
expiry → HTTP 403, XET transport hiccups) and must NOT retry genuine auth/not-found
errors — the previous substring (`"403" in str(exc)`) classifier retried a hard 403
five times (~150s) and mis-handled any message merely containing "403". Logged
exception text must also have URL query strings (presigned-URL credentials) redacted.
"""

from types import SimpleNamespace

from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

from sek8s.system_manager.cache.download import (
    _is_transient_download_error,
    _redact_urls,
)


def _http_error(status: int) -> HfHubHTTPError:
    exc = HfHubHTTPError("http error")
    exc.response = SimpleNamespace(status_code=status)
    return exc


def test_cdn_403_is_transient():
    assert _is_transient_download_error(_http_error(403)) is True


def test_401_unauthorized_is_not_transient():
    assert _is_transient_download_error(_http_error(401)) is False


def test_404_not_found_is_not_transient():
    assert _is_transient_download_error(_http_error(404)) is False


def test_gated_repo_is_not_transient():
    # GatedRepoError is an HfHubHTTPError subclass (often 403) but is a hard auth
    # failure — retrying never succeeds, so it must not be treated as transient.
    assert _is_transient_download_error(GatedRepoError("gated")) is False


def test_repo_not_found_is_not_transient():
    assert _is_transient_download_error(RepositoryNotFoundError("missing")) is False


def test_revision_not_found_is_not_transient():
    assert _is_transient_download_error(RevisionNotFoundError("bad-rev")) is False


def test_plain_exception_mentioning_403_is_not_transient():
    # Regression for the old substring classifier: an unrelated error whose message
    # merely contains "403" must NOT be retried.
    assert _is_transient_download_error(ValueError("computed 403 bytes remaining")) is False


def test_redact_strips_presigned_signature():
    msg = (
        "403 Forbidden for url: "
        "https://cdn-lfs.hf.co/repos/ab/model.bin?X-Amz-Signature=deadbeef&Expires=99"
    )
    out = _redact_urls(msg)
    assert "X-Amz-Signature" not in out
    assert "deadbeef" not in out
    assert "https://cdn-lfs.hf.co/repos/ab/model.bin?<redacted>" in out


def test_redact_leaves_url_without_query_untouched():
    msg = "fetching https://huggingface.co/org/model"
    assert _redact_urls(msg) == msg
