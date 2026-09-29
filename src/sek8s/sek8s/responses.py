from pydantic import BaseModel, Field


class AttestationResponse(BaseModel):
    """Attestation evidence from the guest.

    ``quote`` carries the evidence on every platform: an Intel TDX quote or the raw AMD SEV-SNP
    attestation report, base64. The API identifies which from the bytes themselves, so there is
    no discriminator to keep in step with the blob.
    """

    quote: str = Field(
        ..., description="Base64-encoded TDX quote or SEV-SNP attestation report."
    )

    nvtrust_evidence: str = Field(..., description="")
