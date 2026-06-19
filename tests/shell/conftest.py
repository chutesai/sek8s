"""Harness for driving the boot/cluster-init shell scripts with stubbed external
commands (kubectl/helm/poweroff/...) so their control flow can be tested without a
live cluster. Pure-subprocess; runs in the default `make test-local` / CI unit job.
"""

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def shell(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def stub(name, body):
        """Create an executable stub `name` (bash) earlier on PATH than the real tool."""
        p = bin_dir / name
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)

    def run(script_rel, env=None, require=("bash",)):
        for tool in require:
            if shutil.which(tool) is None:
                pytest.skip(f"required tool not available: {tool}")
        full_env = dict(os.environ)
        full_env["PATH"] = f"{bin_dir}{os.pathsep}{full_env['PATH']}"
        if env:
            full_env.update(env)
        return subprocess.run(
            ["bash", str(REPO / script_rel)],
            env=full_env,
            capture_output=True,
            text=True,
        )

    return SimpleNamespace(tmp=tmp_path, bin=bin_dir, stub=stub, run=run)
