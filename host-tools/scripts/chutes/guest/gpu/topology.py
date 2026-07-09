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


# A profile declares these and detection produces them; the two are compared for
# equality to decide whether a live host is baselined.
TopologyFingerprint = NumaTopology | FlatTopology
