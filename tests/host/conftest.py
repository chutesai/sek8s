import os
import sys

# chutes.guest is not an installed package; add host-tools/scripts/ to sys.path
# so that `from chutes.guest.gpu.profiles import ...` works in tests.
_HOST_SCRIPTS = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "host-tools", "scripts"
)
if os.path.abspath(_HOST_SCRIPTS) not in sys.path:
    sys.path.insert(0, os.path.abspath(_HOST_SCRIPTS))
