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


def _fake_response(status: int) -> SimpleNamespace:
    # huggingface_hub 1.x requires a real Response on HfHubHTTPError and reads
    # .headers/.request during construction; this stub satisfies that and exposes
    # the status_code the classifier inspects.
    return SimpleNamespace(headers={}, request=None, status_code=status)


def _http_error(status: int) -> HfHubHTTPError:
    return HfHubHTTPError("http error", response=_fake_response(status))


def test_cdn_403_is_transient():
    assert _is_transient_download_error(_http_error(403)) is True


def test_401_unauthorized_is_not_transient():
    assert _is_transient_download_error(_http_error(401)) is False


def test_404_not_found_is_not_transient():
    assert _is_transient_download_error(_http_error(404)) is False


def test_gated_repo_is_not_transient():
    # GatedRepoError is an HfHubHTTPError subclass (often 403) but is a hard auth
    # failure — retrying never succeeds, so it must not be treated as transient.
    assert (
        _is_transient_download_error(
            GatedRepoError("gated", response=_fake_response(403))
        )
        is False
    )


def test_repo_not_found_is_not_transient():
    assert (
        _is_transient_download_error(
            RepositoryNotFoundError("missing", response=_fake_response(404))
        )
        is False
    )


def test_revision_not_found_is_not_transient():
    assert (
        _is_transient_download_error(
            RevisionNotFoundError("bad-rev", response=_fake_response(404))
        )
        is False
    )


def test_plain_exception_mentioning_403_is_not_transient():
    # Regression for the old substring classifier: an unrelated error whose message
    # merely contains "403" must NOT be retried.
    assert (
        _is_transient_download_error(ValueError("computed 403 bytes remaining"))
        is False
    )


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
