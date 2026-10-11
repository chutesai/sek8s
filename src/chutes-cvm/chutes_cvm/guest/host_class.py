"""Chutes' record of this host's class: the measurements published for machines like it.

``HostProfile`` is what this machine IS, captured on the host. ``HostClass`` is what Chutes holds
for its class, fetched once from its platform's POST /servers/{tdx,snp}/host_profiles/status: the
class's status and one ``MeasuredImage`` per image ``(version, rc)`` measured for it. Whether an
image set can launch here is ``measured_image`` over that list -- the same join the API's
preflight makes.

Each platform's class parses its own entries (``TdxHostClass``, ``SnpHostClass``), so what an entry
carries follows from the platform rather than from which fields a response happened to contain: a
TDX entry carries only which image it is for (the verifier checks ACPI through RTMR0), and an
SEV-SNP entry always carries the ACPI hash its firmware checks. An entry without what its platform
needs fails here, at retrieval, never later at launch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Self

from chutes_cvm.guest.chutes_api import ChutesApiError, host_class_status
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.image_set import ImageSet
from chutes_cvm.guest.tee import SnpTeeProvider, TdxTeeProvider, TeeProvider

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class HostClassStatus(str, Enum):
    """Where the class is in Chutes' lifecycle. Only ``measurements`` says what can launch: a
    freshly published class can still read ``pending`` while measurements already cover it.
    """

    UNKNOWN = "unknown"  # never submitted: register it
    PENDING = "pending"  # on file, awaiting measurement generation: wait
    ACCEPTED = "accepted"  # measured at some point


class NotMeasured(LookupError):
    """No image ``(version, rc)`` measured for this host class matches ``image``."""

    def __init__(self, image: ImageSet):
        super().__init__(f"no published measurement for {image.label}")
        self.image = image


@dataclass(frozen=True)
class MeasuredImage:
    """One image Chutes measured for this class. Intel TDX needs nothing beyond which image:
    ACPI is attested through RTMR0."""

    version: str
    rc: bool

    @classmethod
    def from_entry(cls, entry: dict, **platform_fields) -> Self:
        """One entry of the API's response; ``platform_fields`` are what the subclass parsed."""
        version = entry.get("version")
        if not isinstance(version, str) or not version:
            raise ChutesApiError(
                f"the API returned a measurement with no version: {entry!r}"
            )
        return cls(version=version, rc=bool(entry.get("rc")), **platform_fields)

    @property
    def label(self) -> str:
        return f"{self.version}{' (rc)' if self.rc else ''}"

    def is_for(self, image: ImageSet) -> bool:
        return self.version == image.version and self.rc == image.rc


@dataclass(frozen=True)
class SnpMeasuredImage(MeasuredImage):
    """AMD SEV-SNP: also the ACPI hash the firmware checks the tables against, which the launch
    puts on the measured cmdline."""

    acpi_sha256: str

    @classmethod
    def from_entry(cls, entry: dict, **platform_fields) -> Self:
        value = entry.get("acpi_sha256")
        if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
            raise ChutesApiError(
                f"the API published SEV-SNP measurement {entry.get('version')!r} without a "
                f"valid acpi_sha256 (got {value!r}); its firmware cannot boot without one"
            )
        return super().from_entry(entry, acpi_sha256=value)


@dataclass(frozen=True)
class HostClass:
    """This host's class as Chutes records it. Each platform's subclass parses its own entries."""

    fingerprint: str
    status: HostClassStatus
    measured: list[MeasuredImage]
    detail: str

    #: The platform whose classes this type parses.
    provider_type: ClassVar[type[TeeProvider]]
    #: The entry type this platform's response carries.
    entry_type: ClassVar[type[MeasuredImage]]

    @classmethod
    def type_for(cls, host_profile: HostProfile) -> "type[HostClass]":
        """The host class type for ``host_profile``'s platform."""
        for class_type in (TdxHostClass, SnpHostClass):
            if isinstance(host_profile.tee_provider, class_type.provider_type):
                return class_type
        raise ValueError(f"no HostClass for {host_profile.tee_provider.label}")

    @classmethod
    def fetch(
        cls,
        host_profile: HostProfile,
        *,
        config_path: str,
        api_base: str,
        target_os: str | None = None,
    ) -> HostClass:
        """Sign ``host_profile`` and fetch its class. ``target_os`` asks about the class the host
        will be after an OS upgrade. Raises ``ChutesApiError`` on any failure to get an answer,
        including an answer missing what this platform's launch needs."""
        response = host_class_status(
            config_path=config_path,
            host_profile=host_profile,
            api_base=api_base,
            target_os=target_os,
        )
        return cls.type_for(host_profile).from_response(response)

    @classmethod
    def from_response(cls, response: dict) -> Self:
        try:
            status = HostClassStatus(response.get("status"))
        except ValueError as exc:
            raise ChutesApiError(
                f"the API returned an unknown host class status: {response.get('status')!r}"
            ) from exc
        return cls(
            fingerprint=str(response.get("fingerprint", "?")),
            status=status,
            measured=[
                cls.entry_type.from_entry(e) for e in response.get("measurements") or []
            ],
            detail=str(response.get("detail", "")),
        )

    def measured_image(self, image: ImageSet) -> MeasuredImage:
        """The published entry for ``image``'s ``(version, rc)``, or ``NotMeasured``."""
        for entry in self.measured:
            if entry.is_for(image):
                return entry
        raise NotMeasured(image)


@dataclass(frozen=True)
class TdxHostClass(HostClass):
    """An Intel TDX class: entries name only the image."""

    provider_type = TdxTeeProvider
    entry_type = MeasuredImage


@dataclass(frozen=True)
class SnpHostClass(HostClass):
    """An AMD SEV-SNP class: every entry carries its ACPI hash."""

    provider_type = SnpTeeProvider
    entry_type = SnpMeasuredImage
