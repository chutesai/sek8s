"""A byte-exact lock on the two things a refactor must never move.

``ImageConfig.to_dict()`` is the COMPLETE input to the tdx-measure fork -- ``generate_acpi_blobs``
hands it that metadata plus the distribution, nothing else -- so a byte-identical dict means a
byte-identical MRTD and RTMR0, provable without the fork or Docker on this machine. The command's
own ``to_args()`` is locked alongside it because that is what a real launch runs.

The AMD cases lock the command; their ``metadata`` is incidental, since SEV-SNP's digest needs no
ACPI dump at all. They are here because one GPU profile now has two TEEs and an accidental change
to either belongs in a diff.

This exists for the launch-assembly refactor: ``test_qemu_command_parity`` compares the
measurement path to the launch path, so it stays green when BOTH move together. This one is an
absolute reference and catches exactly that.

**This test only ever reads.** Regenerating a golden is a deliberate act with consequences for
every host in the fleet, so it lives in ``scripts/update_measurement_golden.py`` and is run by
hand -- never as a side effect of running the suite.
"""

import json

import pytest
from golden_cases import CASES, golden_path, snapshot


@pytest.mark.parametrize("name", sorted(CASES))
def test_measurement_inputs_are_unchanged(name):
    path = golden_path(name)
    assert path.exists(), (
        f"no golden for {name}; create it with scripts/update_measurement_golden.py and review "
        "the diff before committing"
    )
    assert snapshot(name) == json.loads(path.read_text()), (
        f"{name}: the measurement input changed, so every published measurement for this class "
        "is now wrong and those hosts stop attesting. If the change is intended, run "
        "scripts/update_measurement_golden.py, read the diff, and republish the measurements in "
        "the same rollout."
    )
