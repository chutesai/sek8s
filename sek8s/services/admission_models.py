"""Typed Pydantic models for Kubernetes AdmissionReview and SubjectAccessReview."""

from typing import List, Optional

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Admission (validate / mutate)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Authorization (SubjectAccessReview)
# ---------------------------------------------------------------------------


class ResourceAttributes(BaseModel):
    namespace: str = ""
    verb: str = ""
    group: str = ""
    version: str = ""
    resource: str = ""
    subresource: str = ""
    name: str = ""


class SubjectAccessReviewSpec(BaseModel):
    user: str = ""
    groups: Optional[List[str]] = None
    resourceAttributes: Optional[ResourceAttributes] = None


class SubjectAccessReviewRequest(BaseModel):
    apiVersion: str = "authorization.k8s.io/v1"
    kind: str = "SubjectAccessReview"
    spec: SubjectAccessReviewSpec = SubjectAccessReviewSpec()


class AuthorizationStatus(BaseModel):
    allowed: bool = False
    denied: bool = False
    reason: Optional[str] = None


class SubjectAccessReviewResponse(BaseModel):
    apiVersion: str = "authorization.k8s.io/v1"
    kind: str = "SubjectAccessReview"
    status: AuthorizationStatus

    @classmethod
    def no_opinion(cls) -> "SubjectAccessReviewResponse":
        return cls(status=AuthorizationStatus())

    @classmethod
    def denied(cls, reason: str) -> "SubjectAccessReviewResponse":
        return cls(status=AuthorizationStatus(denied=True, reason=reason))
