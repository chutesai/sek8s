"""What one launch materialized for one guest.

``LaunchConfig`` is declared intent -- the sizes, paths and hostname an operator wrote down.
This is materialized fact: the per-VM image copy that was made, the tap device that was created,
the volumes that now exist. The orchestrator's Step 3 produces one; the boot primitive consumes
it. They used to travel between the two as a flattened argv list that argparse immediately parsed
back, which is how ``network.ssh_port`` came to be configurable but never plumbed.

The host half of a launch is ``HostProfile`` -- what this machine IS. This is the other half:
what this guest NEEDS. ``QemuCommand.create`` takes exactly those two.
"""

from dataclasses import dataclass

from chutes_cvm.guest.qemu import GuestNetwork, GuestVolumes


@dataclass(frozen=True)
class GuestContext:
    """One guest's resolved resources and launch options.

    Deliberately not a ``Profile``: in this codebase that word means the captured, fingerprinted
    thing that feeds measurement. Nothing here is measured -- ``ImageConfig`` replaces every drive
    with a backing-free filler, so only the pcie.0 slots these occupy reach the DSDT.
    """

    #: The per-VM qcow2 copy, not the base image it was copied from.
    image: str
    #: Resolved volume paths; a benchmark guest has no cache volume.
    volumes: GuestVolumes
    #: The tap device that was created, or user-mode with its forwarded port.
    network: GuestNetwork
    #: Whether to bind and attach this host's GPUs.
    pass_gpus: bool
    #: Run QEMU in the foreground rather than daemonizing.
    foreground: bool
    #: Print the SSH login hint after launch (benchmark and debug guests).
    show_ssh: bool = False
