"""Lab-validated host topologies (Ubuntu × GPU SKU × GPU count).

Host *profiles* (PPA/kernel/packages) can apply to an OS version without every
hardware SKU having been end-to-end tested on that OS. This module records only
combinations that have been explicitly validated in the field.

Keys use GpuProfile ``name`` values (e.g. ``H200``, ``RTX_PRO_6000``).
"""

# (ubuntu_version, gpu_sku, gpu_count) — gpu_sku matches GpuProfile.name
_VALIDATED_TOPOLOGIES: frozenset[tuple[str, str, int]] = frozenset(
    {
        ("25.10", "H200", 8),
        ("25.10", "B200", 8),
        ("25.10", "RTX_PRO_6000", 8),
        ("26.04", "H200", 8),
        ("26.04", "B200", 8),
        ("26.04", "RTX_PRO_6000", 8),
    }
)

_GPU_SKU_LABEL: dict[str, str] = {
    "H200": "H200",
    "B200": "B200",
    "B300": "B300",
    "RTX_PRO_6000": "RTX Pro 6000",
}

# Per-row notes for operators (keep in sync with docs expectations).
_HGX_NOTE = (
    "Host-side Fabric Manager required. CX7 NVSwitch bridge PFs stay on host "
    "(excluded from passthrough)."
)
_VALIDATED_NOTES: dict[tuple[str, str, int], str] = {
    ("25.10", "H200", 8): "NVSwitch required.",
    ("25.10", "B200", 8): _HGX_NOTE,
    ("25.10", "RTX_PRO_6000", 8): "No NVSwitch on this SKU.",
    ("26.04", "H200", 8): "NVSwitch required.",
    ("26.04", "B200", 8): _HGX_NOTE,
    ("26.04", "RTX_PRO_6000", 8): "No NVSwitch on this SKU.",
}


def is_validated_topology(
    ubuntu_version: str,
    gpu_sku: str,
    gpu_count: int,
) -> bool:
    """Return True if this (OS, SKU, count) triple is in the validated matrix."""
    return (ubuntu_version, gpu_sku, gpu_count) in _VALIDATED_TOPOLOGIES


def validated_topology_rows() -> list[tuple[str, str, int]]:
    """Sorted list of (ubuntu, gpu_sku, count) for documentation and CLI."""
    return sorted(_VALIDATED_TOPOLOGIES, key=lambda r: (r[0], r[1], r[2]))


def format_topology_matrix() -> str:
    """Plain-text table for README / ``setup-tdx-host --topology-matrix``."""
    lines = [
        "Validated host topologies (end-to-end tested: TDX host + VM + GPUs)",
        "",
        f"{'Ubuntu':<8} {'GPU SKU':<16} {'GPUs':<6} Status",
        "-" * 48,
    ]
    for ubuntu, sku, n in validated_topology_rows():
        label = _GPU_SKU_LABEL.get(sku, sku)
        lines.append(f"{ubuntu:<8} {label:<16} {n:<6} validated")
    lines.append("-" * 48)
    lines.append("")
    lines.append("Notes:")
    for row in validated_topology_rows():
        note = _VALIDATED_NOTES.get(row)
        if note:
            ubuntu, sku, n = row
            label = _GPU_SKU_LABEL.get(sku, sku)
            lines.append(f"  • {ubuntu} / {label} / {n}: {note}")
    lines.extend(
        [
            "",
            "Any other (Ubuntu, SKU, count) combination may still work — host profiles",
            "are OS-driven — but is not listed as validated until added above.",
        ]
    )
    return "\n".join(lines)
