from pydantic import BaseModel, Field


class AttestationResponse(BaseModel):

    tdx_quote: str = Field(..., description="")

    nvtrust_evidence: str = Field(..., description="")