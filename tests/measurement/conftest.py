import os
import sys

# The measurement engine and the launcher's arg builders now live in one package,
# chutes_cvm (src/chutes-cvm). Put it on sys.path so the tests import
# chutes_cvm.measurement.* and chutes_cvm.guest.* without an install.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
for _p in (os.path.join(_ROOT, "src", "chutes-cvm"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


import pytest  # noqa: E402  (after the sys.path setup above)


@pytest.fixture
def tdx_platform():
    """A TdxMeasurements with its image-level registers faked: RTMR1/2 need the tdx-measure
    fork and RTMR3 needs a mountable image, neither of which a unit test has."""
    from unittest.mock import patch

    from chutes_cvm.measurement import tdx

    with patch.object(tdx, "compute_rtmr1_2", return_value=("R1", "R2")), patch.object(
        tdx, "compute_rtmr3", return_value=("R3", [])
    ):
        return tdx.TdxMeasurements(
            bios_dir="/fw", tdx_measure_bin="tdx-measure", dist="x", image="/i.qcow2"
        )
