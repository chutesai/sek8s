"""The ``ImageConfig`` handed to tdx-measure: a rendering of an already dump-shaped command.

``QemuCommand.for_measurement`` (``MeasurementCommandBuilder``) builds a command whose machine,
memory backends, emulated devices, endpoints, serial and ``-cpu`` are already what the dumper
needs. This only renders it as the metadata JSON.

It used to *rewrite* a launch-shaped command instead -- seven substitutions over something it was
handed -- which was brittle in one direction only, and the dangerous one: anything added to the
launch command that this did not know to strip silently entered the bytes the fork hashes,
changing every published measurement. Deriving the ``iommufd`` object did exactly that, and only
a byte-exact golden caught it. Building each command for its purpose removes the failure mode
rather than guarding it, and takes the endpoint reverse-lookup with it -- ``_endpoint_for`` used
to regex ``rp3`` back into "the third GPU" to recover BARs the builder was holding all along.

tdx-measure does the dumping itself inside its container; this only produces its input.
Reproduces a real launch's measured ``etc/acpi/tables`` byte-for-byte with no GPU present
(validated against box-028).
"""

from dataclasses import dataclass

from chutes_cvm.guest.host_profile import HostProfile
from chutes_cvm.guest.qemu import QemuCommand


@dataclass
class ImageConfig:
    """Build from ``QemuCommand.for_measurement`` + the same ``HostProfile``; ``to_dict()`` is the
    metadata JSON. Reads the command's structured fields -- no re-parsing, no substitution.
    """

    cmd: QemuCommand
    host: HostProfile
    acpi_tables: str
    with_smbios: bool = True

    def to_dict(self) -> dict:
        cmd = self.cmd
        return {
            "boot_config": {
                "cpus": int(cmd.smp_topology.split(",", 1)[0]),
                "memory": cmd.mem,
                "bios": cmd.firmware,
                "acpi_tables": self.acpi_tables,
                "qemu": {
                    "machine": cmd.machine,
                    "cpu": cmd.cpu_args,
                    "accel": cmd.accel,
                    "smp": cmd.smp_topology,
                    "objects": cmd.objects,
                    "numa": cmd.numa,
                    "smbios": cmd.smbios if self.with_smbios else [],
                    "serial": cmd.serial,
                    "devices": cmd.devices,
                    "fw_cfg": cmd.fw_cfg,
                    # Pin the SMBIOS Type-4 Processor ID (#14) to the production CPUID;
                    # tdx-measure patches it into the dumped SMBIOS (KVM can't override the
                    # generating host's CPUID). None => unpatched.
                    "processor_id": self.host.cpu.processor_id,
                },
            },
            "direct": {
                "kernel": cmd.kernel,
                "initrd": cmd.initrd,
                "cmdline": cmd.append,
            },
        }
