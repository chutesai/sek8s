from typing import Optional

from pydantic import BaseModel, Field, model_validator


class AttestationResponse(BaseModel):
    """Attestation evidence from the guest.

    The evidence field is named for the TEE that produced it rather than carried
    alongside a separate ``tee_type`` discriminator: the field *is* the type, so a
    payload cannot claim one platform while carrying another's evidence, and there
    is no discriminator to keep in sync with the blob.

    ``tdx_quote`` keeps its original name and stays populated on TDX hosts, so
    verifiers written before AMD support keep working unchanged.
    """

    tdx_quote: Optional[str] = Field(
        None, description="Base64-encoded Intel TDX quote. Set only on TDX hosts."
    )

    snp_quote: Optional[str] = Field(
        None,
        description="Base64-encoded AMD SEV-SNP attestation report. Set only on SEV-SNP hosts.",
    )

    nvtrust_evidence: str = Field(..., description="")

    @model_validator(mode="after")
    def _exactly_one_quote(self) -> "AttestationResponse":
        present = [
            name
            for name, value in (
                ("tdx_quote", self.tdx_quote),
                ("snp_quote", self.snp_quote),
            )
            if value
        ]
        if len(present) != 1:
            raise ValueError(
                "exactly one of tdx_quote or snp_quote must be set, got "
                f"{present or 'neither'}"
            )
        return self

    @property
    def tee_type(self) -> str:
        """Which TEE produced the evidence, derived from which field is set."""
        return "tdx" if self.tdx_quote else "snp"

    @property
    def quote(self) -> str:
        """The evidence blob, whichever field carries it."""
        return self.tdx_quote or self.snp_quote or ""
