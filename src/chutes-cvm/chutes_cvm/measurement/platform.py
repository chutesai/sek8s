"""What every TEE's measurement generator shares.

A release is measured once per platform. ``PlatformMeasurements`` is the base each platform
implements (``tdx.TdxMeasurements``, ``snp.SnpMeasurements``); this module also holds the two
pieces both of them use: the error type, and the reader of the image's staged direct-boot
artifacts.
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod

from chutes_cvm.guest.direct_boot import direct_boot_artifacts
from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.tee import TeeProvider


class MeasurementError(RuntimeError):
    """A measurement could not be computed (missing input, tool error, parse failure)."""


def staged_boot_artifacts(image: str) -> tuple[str, str, str]:
    """``(kernel, initrd, cmdline)`` staged beside ``image``, read as the launcher reads them.

    It is the launcher's reader, not a copy of it: what the launcher hands QEMU is what the
    hardware measures, so RTMR1/2 and the SEV-SNP digest must see the same kernel, initrd and
    cmdline. Paths are absolute, because the tdx-measure fork opens them relative to its own
    metadata file rather than this process's cwd.
    """
    try:
        return direct_boot_artifacts(os.path.abspath(image))
    except FileNotFoundError as exc:
        raise MeasurementError(str(exc)) from exc


class PlatformMeasurements(ABC):
    """One TEE's measurements for one release.

    What every class of the platform shares is loaded once, in the constructor, so a missing
    release input fails the release rather than any one class. ``add`` measures one host class
    into ``hardware``; ``section`` is the platform's part of the version entry.
    """

    #: This platform's key in the version entry.
    key: str
    #: The provider a class derives (from its CPU vendor) to be measured here.
    provider: type[TeeProvider]

    def __init__(self) -> None:
        self.hardware: list[dict] = []

    @abstractmethod
    def measure(self, host: HostProfile) -> dict:
        """The class-specific value for one hardware entry, e.g. ``{"rtmr0": ...}``."""

    @abstractmethod
    def section(self) -> dict:
        """This platform's section of the version entry."""

    def add(self, host: HostProfile, fingerprint: str) -> dict:
        """Measure one class and record its entry. The API's fingerprint is carried through
        (never recomputed) so the reconciler can join it to the submitted host profile.
        """
        profile, gpu_count = host.gpu_profile, host.gpu_count
        measured = self.measure(host)
        entry = {
            "name": f"{profile.display_name} [{host.qemu_version}, {host.variant_label}]",
            "description": (
                f"{gpu_count}x {profile.expected_gpus[0].upper()} GPU configuration"
            ),
            "fingerprint": fingerprint,
            "expected_gpus": list(profile.expected_gpus),
            "gpu_count": gpu_count,
            **measured,
        }
        self.hardware.append(entry)
        values = "  ".join(f"{k}={v[:16]}…" for k, v in measured.items())
        print(f"    {entry['name']}  fp={fingerprint[:12]}…  {values}", file=sys.stderr)
        return entry
