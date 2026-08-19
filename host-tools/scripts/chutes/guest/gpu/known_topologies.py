"""Registry of known host topologies — the CpuTopology / TopologyFingerprint values
each GPU profile is baselined for.

Kept out of the profiles so ``profiles.py`` stays GPU-model policy and just imports
the fingerprints it has registered. Each value here corresponds to a real host class
captured with ``discover-profile.sh``:

  - ``CpuTopology`` constants = one host CPU class (vcpus = host_cpus −
    host_reserved_cpus; sockets; CPU identity).
  - ``TopologyFingerprint`` constants = a CpuTopology + guest RAM (``mem_gb``, from the
    profile's guest_mem_gb rule) + the GpuTopology that host presents.

``cpu_processor_id=None`` marks a PLACEHOLDER fingerprint whose exact CPU model is
pending a discover-profile.sh capture on that host class: it never matches a live host,
so the profile is refused at launch (and offline generation refuses it) until the real
value is filled in.
"""

from chutes.guest.gpu.topology import (
    CpuTopology,
    FlatTopology,
    NumaTopology,
    TopologyFingerprint,
)

# ── CPU shapes (one per known GPU × host class) ─────────────────────────────────

# H200 dev-h200-tee: 128 CPUs − 4 reserved → 124 vcpus. Intel Emerald Rapids (family
# 6/model 207, CPUID leaf-1 0x000c06f2 / EDX 0x1fa9fbff) — validated end-to-end.
H200_EMERALD = CpuTopology(
    vcpus=124, sockets=2, cpu_vendor="GenuineIntel", cpu_processor_id="f2060c00fffba91f"
)
# B200 on a 192-CPU Xeon: 192 − 16 reserved → 176 vcpus.
B200_XEON = CpuTopology(vcpus=176, sockets=2, cpu_vendor="GenuineIntel")
# B200 on a 288-CPU Xeon 6 (SNC off → 2 NUMA nodes): 272 vcpus.
B200_XEON6 = CpuTopology(vcpus=272, sockets=2, cpu_vendor="GenuineIntel")
# RTX Pro 6000 (HPE DL380a Gen12): 128-CPU 2-socket Intel Xeon, no SMT
# (threads_per_core=1) — Sierra Forest E-core class, family 6/model 0xAF/stepping 3
# (CPUID leaf-1 0x000a06f3 / EDX 0x1fa9fbff). 128 − 4 reserved → 124 vcpus. Captured
# from discover-profile.sh on eu1-hpe1-rtx6000pro-se-008 (local/profiles/rtx-pro-6000.json).
RTX_XEON = CpuTopology(
    vcpus=124, sockets=2, cpu_vendor="GenuineIntel", cpu_processor_id="f3060a00fffba91f"
)

# ── Full fingerprints (CpuTopology × guest RAM × GpuTopology) ────────────────────

# H200 8-GPU: 141×8 = 1128 GB guest RAM; GPUs always 4+4; the two variants differ only
# in which host NUMA node the four NVSwitches attach to (chassis-dependent).
H200_KR6288 = TopologyFingerprint(  # NVSwitches on node 0 (e.g. KR6288)
    H200_EMERALD,
    1128,
    NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(0, 0, 0, 0)),
)
H200_XE9680 = TopologyFingerprint(  # NVSwitches on node 1 (e.g. Dell XE9680)
    H200_EMERALD,
    1128,
    NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1), nvswitch_nodes=(1, 1, 1, 1)),
)

# B200 8-GPU: same 4+4 GPU layout, no NVSwitch/IB — one profile, two host classes with
# different guest RAM ((host−64)//8×8: ~1944 / ~2952 GB). cpu_processor_id PENDING a
# discover-profile.sh capture on each.
B200_XEON_FP = TopologyFingerprint(
    B200_XEON, 1944, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)
B200_XEON6_FP = TopologyFingerprint(
    B200_XEON6, 2952, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)

# RTX Pro 6000 8-GPU: guest RAM pinned to VRAM (96×8 = 768 GB; RTX pins mem to VRAM,
# only B200 RAM-derives — so the host's ~2 TB RAM is intentionally not all handed to the
# guest). 2 NUMA nodes → guest-NUMA path (GPUs 4+4); >2 nodes → flat fallback (only GPU
# count matters).
RTX_NUMA = TopologyFingerprint(
    RTX_XEON, 768, NumaTopology(gpu_nodes=(0, 0, 0, 0, 1, 1, 1, 1))
)
RTX_FLAT = TopologyFingerprint(RTX_XEON, 768, FlatTopology(gpu_count=8))
