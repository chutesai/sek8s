"""GPU evidence nonce validation: exactly 64 hex characters."""

import pytest

from chutes_nvevidence.exceptions import NonceError
from chutes_nvevidence.util import validate_nonce


def test_accepts_a_valid_nonce():
    assert validate_nonce("A" * 64) == "a" * 64


@pytest.mark.parametrize("gap", [" ", "\t", "\n"], ids=["space", "tab", "newline"])
def test_rejects_embedded_whitespace(gap):
    """64 characters, but whitespace between byte pairs is skipped by bytes.fromhex, so 62 hex
    digits and a 2-character gap would decode to 31 bytes."""
    with pytest.raises(NonceError, match="hexadecimal"):
        validate_nonce("a" * 30 + gap * 2 + "a" * 32)
