"""Materializing a guest's disk image: verify the published set, then copy it per VM.

Stage 3 of a launch. The base image is downloaded once and shared; each VM boots its own copy so
a relaunch cannot inherit a previous guest's writes. The direct-boot sidecars (kernel, initrd,
cmdline) are staged beside it -- the same bytes ``measurements generate`` measured, so the boot
matches the published RTMR1/2.
"""

import glob
import json
import os
import sys

from chutes_cvm.guest.image_set import ImageSet
from chutes_cvm.guest.privileged import LaunchError, run

#: Published alongside the image; staged next to the per-VM copy so OVMF can boot
#: them directly -- the same bytes `measurements generate` measured.
_DIRECT_BOOT_SIDECARS = ("vmlinuz", "initrd", "cmdline")


def prepare_vm_image(image_set_dir: str, hostname: str, vm_image_dir: str) -> str:
    """Verify the image set and instantiate the per-VM copy; return the per-VM image path.

    The per-VM image is a full copy of the base qcow2 (not an overlay): luksRemoveKey later
    destroys the old key slot in-place on the only copy, matching the storage/cache volumes.

    Python owns the decisions/data — verify the set against its manifest and resolve the qcow2 +
    its manifest sha256 (``ImageSet``), derive the per-VM name, and pick which stale copies
    to reap. The file mutations are privileged (the image dir is root-owned under /var/lib/chutes),
    so each runs via sudo, matching the per-step-sudo pattern the rest of launch uses.
    """
    try:
        image = ImageSet.from_dir(image_set_dir)
        image.verify(full=False)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise LaunchError(f"image set verification failed: {exc}") from exc
    qcow2, sha256 = image.qcow2.path, image.qcow2.sha256
    print(
        f"Verified image set via manifest: {qcow2} (sha256={sha256})", file=sys.stderr
    )

    if not os.path.isdir(vm_image_dir):
        run(["sudo", "mkdir", "-p", vm_image_dir])

    vm_image = os.path.join(vm_image_dir, f"tdx-{hostname}-{sha256[:16]}.qcow2")

    # Reap stale per-VM images (and their sidecars) from previous base versions for this host.
    for stale in sorted(
        glob.glob(os.path.join(vm_image_dir, f"tdx-{hostname}-*.qcow2"))
    ):
        if stale == vm_image:
            continue
        print(f"Removing stale VM image: {stale}", file=sys.stderr)
        stale_base = stale[: -len(".qcow2")]
        run(
            ["sudo", "rm", "-f", stale]
            + [f"{stale_base}.{ext}" for ext in _DIRECT_BOOT_SIDECARS]
        )

    if os.path.exists(vm_image):
        print(f"Using existing VM image: {vm_image}", file=sys.stderr)
    else:
        print(f"Copying base image to per-VM image: {vm_image}", file=sys.stderr)
        run(["sudo", "cp", qcow2, vm_image])

    # Direct-boot sidecars must travel with the per-VM copy the launcher boots (it resolves
    # <image-base>.{vmlinuz,initrd,cmdline} next to that copy). Re-sync unconditionally so a
    # reused per-VM image also refreshes. ``verify`` already refused a set missing any of them.
    vm_no_ext = vm_image[: -len(".qcow2")]
    for sidecar in (image.vmlinuz, image.initrd, image.cmdline):
        run(["sudo", "cp", sidecar.path, f"{vm_no_ext}.{sidecar.role}"])

    return vm_image
