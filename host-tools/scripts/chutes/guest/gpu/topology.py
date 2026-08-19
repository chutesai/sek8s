"""Topology fingerprints: the RTMR0-distinguishing shape of a host's passed-through devices.

RTMR0 = f(guest ACPI, QEMU). Two hosts on the *same* GpuProfile diverge in RTMR0
only when their passed-through device topology differs, because that topology is
what drives the guest NUMA / PXB-PCIe layout QEMU emits into the guest ACPI. These
classes capture exactly that shape, so a profile can declare which topologies it
has a registered measurement for (``baselined_measurements``) and detection can
fingerprint a live host (``host_topology_fingerprint``) and compare the two.

They are value types (frozen dataclasses): hashable and compared by field value,
so they live in sets and support ``fingerprint in baselined_topologies``. A
``NumaTopology`` never equals a ``FlatTopology`` (different classes) — that is the
"landed on the 2-node guest-NUMA path" vs "flat fallback" discriminator, replacing
the old positional ``("numa", ...)`` / ``("flat", ...)`` string tag.

Only *device* topology is captured — NOT CPU / socket / RAM counts. Those feed
RTMR0 too, but a GpuProfile pins them to constants (fixed vcpus, ``-smp``, RAM),
so they are identical across every host of a given profile; only the device
NUMA/PXB layout varies host to host. A host with a different physical CPU count
(e.g. an SMT host with twice the logical CPUs) therefore shares a fingerprint with
its siblings, because the profile still hands the guest the same fixed ``-smp``.
"""

from dataclasses import dataclass


def _node_sig(nodes: tuple[int, ...]) -> str:
    """Compact signature of a per-device NUMA-node vector: ``node{n}`` when every
    device sits on one node (the common case, e.g. ``(0,0,0,0)`` -> ``node0``),
    else the raw per-device vector (e.g. ``(0,0,1,1)`` -> ``0011``)."""
    if len(set(nodes)) == 1:
        return f"node{nodes[0]}"
    return "".join(str(n) for n in nodes)


@dataclass(frozen=True)
class NumaTopology:
    """Guest-NUMA path (host has exactly 2 NUMA nodes and the profile enables it).

    Each field is the per-device host NUMA node, in sorted-BDF order. The
    device->NUMA layout drives QEMU's guest PXB-PCIe grouping and thus RTMR0, so
    the exact node vectors matter, not just how many devices there are. An empty
    tuple means that device class is not passed through for this profile (e.g. no
    NVSwitches / no IB).
    """

    gpu_nodes: tuple[int, ...]
    nvswitch_nodes: tuple[int, ...] = ()
    ib_nodes: tuple[int, ...] = ()

    @property
    def variant_label(self) -> str:
        """Deterministic, human-readable variant id computed from the fingerprint
        (guest-NUMA path). The profile + gpu count + QEMU version prepend the rest
        of the teeMeasurements hardware name, so this must be unique per
        profile+qemu (asserted at generation time)."""
        parts = ["numa"]
        if self.nvswitch_nodes:
            parts.append("nvsw-" + _node_sig(self.nvswitch_nodes))
        if self.ib_nodes:
            parts.append("ib-" + _node_sig(self.ib_nodes))
        return "-".join(parts)


@dataclass(frozen=True)
class FlatTopology:
    """Flat fallback (host is not 2-NUMA-node, or the profile disables guest NUMA).

    The guest is a single flat node with no PXB grouping, so RTMR0 depends only on
    how many of each device is passed through — not which host NUMA node each sits
    on. Counts default to 0 for device classes this profile does not pass through.
    """

    gpu_count: int
    nvswitch_count: int = 0
    ib_count: int = 0

    @property
    def variant_label(self) -> str:
        """Deterministic variant id for the flat (single-node) path. ``nvswN`` /
        ``ibN`` here are device *counts* (flat has no per-device node vector)."""
        parts = ["flat"]
        if self.nvswitch_count:
            parts.append(f"nvsw{self.nvswitch_count}")
        if self.ib_count:
            parts.append(f"ib{self.ib_count}")
        return "-".join(parts)


# A profile declares these and detection produces them; the two are compared for
# equality to decide whether a live host is baselined.
TopologyFingerprint = NumaTopology | FlatTopology
