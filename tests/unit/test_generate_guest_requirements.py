"""Tests for scripts/generate_guest_requirements.py.

The guest venv is measured into RTMR3, so the generated pins decide the measurement. These
cover the cases that would silently change it: group filtering, closure walking, the two
shapes poetry uses for markers, and the refusal to emit an unpinnable package.
"""

import importlib.util
import re
import textwrap
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_guest_requirements.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "generate_guest_requirements", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = load_module()


@pytest.fixture
def lock_file(tmp_path):
    """Write a lock fixture and return its path."""

    def _write(body: str) -> Path:
        path = tmp_path / "poetry.lock"
        path.write_text(textwrap.dedent(body))
        return path

    return _write


def test_normalize_matches_pep503_forms():
    assert gen.normalize("Foo_Bar") == "foo-bar"
    assert gen.normalize("foo.bar") == "foo-bar"
    assert gen.normalize("FOO") == "foo"


def test_load_lock_excludes_other_groups(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "runtime-pkg"
        version = "1.0"
        groups = ["main"]
        files = []

        [[package]]
        name = "dev-only"
        version = "2.0"
        groups = ["dev"]
        files = []
        """
    )
    lock = gen.load_lock(path)
    assert "runtime-pkg" in lock
    assert "dev-only" not in lock


def test_resolve_closure_is_transitive_and_reports_gaps(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "top"
        version = "1.0"
        groups = ["main"]
        files = []
        [package.dependencies]
        middle = ">=1.0"

        [[package]]
        name = "middle"
        version = "1.0"
        groups = ["main"]
        files = []
        [package.dependencies]
        leaf = "*"

        [[package]]
        name = "leaf"
        version = "1.0"
        groups = ["main"]
        files = []

        [[package]]
        name = "unrelated"
        version = "1.0"
        groups = ["main"]
        files = []
        """
    )
    lock = gen.load_lock(path)

    resolved, missing = gen.resolve_closure({"top"}, lock)
    assert resolved == {"top", "middle", "leaf"}
    assert "unrelated" not in resolved
    assert missing == set()

    _, missing = gen.resolve_closure({"absent"}, lock)
    assert missing == {"absent"}


def test_resolve_closure_survives_dependency_cycle(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "a"
        version = "1.0"
        groups = ["main"]
        files = []
        [package.dependencies]
        b = "*"

        [[package]]
        name = "b"
        version = "1.0"
        groups = ["main"]
        files = []
        [package.dependencies]
        a = "*"
        """
    )
    resolved, missing = gen.resolve_closure({"a"}, gen.load_lock(path))
    assert resolved == {"a", "b"}
    assert missing == set()


def test_extras_are_not_walked(lock_file):
    """Optional extras must not drag docs/test packages into the measured venv."""
    path = lock_file(
        """
        [[package]]
        name = "core"
        version = "1.0"
        groups = ["main"]
        files = []
        [package.extras]
        docs = ["sphinx (>=7.0)"]
        """
    )
    resolved, _ = gen.resolve_closure({"core"}, gen.load_lock(path))
    assert resolved == {"core"}


def test_marker_accepts_string_and_group_table():
    assert (
        gen.marker_for({"markers": 'sys_platform == "linux"'})
        == 'sys_platform == "linux"'
    )
    # Poetry writes a table when a marker applies only to certain groups.
    assert gen.marker_for({"markers": {"dev": 'sys_platform == "win32"'}}) is None
    assert gen.marker_for({"markers": {"main": 'python_version < "3.13"'}}) == (
        'python_version < "3.13"'
    )
    assert gen.marker_for({}) is None


def test_render_pins_versions_hashes_and_markers(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "sample"
        version = "1.2.3"
        groups = ["main"]
        markers = "python_version < \\"3.13\\""
        files = [
            {file = "sample-1.2.3.tar.gz", hash = "sha256:bbb"},
            {file = "sample-1.2.3-py3-none-any.whl", hash = "sha256:aaa"},
        ]
        """
    )
    lock = gen.load_lock(path)
    rendered = gen.render({"sample"}, lock)

    assert 'sample==1.2.3 ; python_version < "3.13" \\' in rendered
    # Sorted so regeneration is stable regardless of lock ordering.
    assert rendered.index("--hash=sha256:aaa") < rendered.index("--hash=sha256:bbb")
    assert rendered.rstrip().endswith("--hash=sha256:bbb")


def test_render_skips_workspace_path_dependencies(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "sek8s-common"
        version = "0.1.0"
        groups = ["main"]
        files = []
        [package.source]
        type = "directory"
        url = "src/sek8s-common"
        """
    )
    lock = gen.load_lock(path)
    rendered = gen.render({"sek8s-common"}, lock)
    assert "sek8s-common" not in rendered.replace(gen.HEADER, "")


def test_render_refuses_package_without_hashes(lock_file):
    path = lock_file(
        """
        [[package]]
        name = "unpinnable"
        version = "9.9"
        groups = ["main"]
        files = []
        """
    )
    lock = gen.load_lock(path)
    with pytest.raises(SystemExit, match="no sha256 hashes"):
        gen.render({"unpinnable"}, lock)


def test_committed_requirements_are_current():
    """The checked-in file must match poetry.lock, or the built image drifts from the repo."""
    assert gen.OUTPUT_PATH.exists(), "run: make guest-requirements"
    assert (
        gen.OUTPUT_PATH.read_text() == gen.build()
    ), "stale; run: make guest-requirements"


def test_poetry_core_pins_are_aligned():
    """One poetry-core version everywhere.

    It is the build backend for the editable installs and stamps its version into each
    dist-info/WHEEL, which lands in the RTMR3-measured venv. It is not in poetry.lock
    (a [build-system] requirement is not a locked dependency), so nothing else catches
    the guest install and the packages' declarations drifting apart.
    """
    repo_root = gen.REPO_ROOT
    pin = re.compile(r'requires\s*=\s*\["poetry-core==([^"]+)"\]')

    # Packages sit at two depths: nvevidence/ at one, src/*/ at two.
    pyprojects = [
        repo_root / "pyproject.toml",
        *repo_root.glob("*/pyproject.toml"),
        *repo_root.glob("*/*/pyproject.toml"),
    ]
    declared = {}
    for pyproject in pyprojects:
        match = pin.search(pyproject.read_text())
        if match:
            declared[str(pyproject.relative_to(repo_root))] = match.group(1)

    # Guards against a glob that quietly stops matching: every pyproject must carry the pin.
    assert len(declared) == len(pyprojects), (
        f"pyprojects without a pinned poetry-core: "
        f"{sorted({str(p.relative_to(repo_root)) for p in pyprojects} - set(declared))}"
    )
    assert len(set(declared.values())) == 1, f"poetry-core pins disagree: {declared}"

    install_task = (
        repo_root / "ansible/guest/roles/sek8s/tasks/install-sek8s.yml"
    ).read_text()
    installed = re.search(r"poetry-core==([\w.]+)", install_task)
    assert installed, "install-sek8s.yml does not pin poetry-core"
    assert installed.group(1) == next(iter(set(declared.values()))), (
        f"install-sek8s.yml pins poetry-core=={installed.group(1)} "
        f"but pyprojects declare {set(declared.values())}"
    )
