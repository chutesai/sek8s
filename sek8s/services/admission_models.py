"""Typed Pydantic models for Kubernetes AdmissionReview responses."""

from typing import List, Optional

from pydantic import BaseModel


class AdmissionStatus(BaseModel):
    message: str


class AdmissionResponseBody(BaseModel):
    uid: str
    allowed: bool
    status: Optional[AdmissionStatus] = None
    warnings: Optional[List[str]] = None
    patchType: Optional[str] = None
    patch: Optional[str] = None


class AdmissionReviewResponse(BaseModel):
    apiVersion: str = "admission.k8s.io/v1"
    kind: str = "AdmissionReview"
    response: AdmissionResponseBody
