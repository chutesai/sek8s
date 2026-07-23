import os
import sys

# The measurement tooling lives in guest-tools/measurement/ (flat modules like
# ccel_replay, acpi_bytediff, smbios_match) and imports the launcher's arg
# builders from host-tools/scripts (chutes.guest.qemu) — a one-way dependency.
# Neither is an installed package, so put both on sys.path for the tests.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
for _p in (
    os.path.join(_ROOT, "guest-tools", "measurement"),
    os.path.join(_ROOT, "guest-tools", "measurement", "utils"),
    os.path.join(_ROOT, "host-tools", "scripts"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)
