"""Tests for 04-helm-chart-upgrade.sh fail-closed determinism.

Every measured chart is fatal: a node must converge to the measured chart spec or
power off (no best-effort tier, and no silent pass on "release not found" or a
helm-list error). An unchanged spec must skip without calling helm upgrade. Driven
with stub helm/curl/poweroff so the control flow can be checked without a cluster.
"""

import hashlib

SCRIPT = "ansible/guest/roles/k3s/files/cluster-init/04-helm-chart-upgrade.sh"

CONF = (
    'RELEASE="testchart"\nNAMESPACE="testns"\nCHART="repo/testchart"\nVERSION="1.2.3"\n'
)

STUB_HELM = """
case "$1" in
  repo) exit 0 ;;
  list)
    if [ -n "${HELM_LIST_STATUS:-}" ]; then
      echo "[{\\"name\\":\\"testchart\\",\\"status\\":\\"$HELM_LIST_STATUS\\"}]"
    else
      echo "[]"
    fi
    exit 0 ;;
  upgrade)
    echo "upgrade $*" >> "$HELM_UPGRADE_RECORD"
    exit "${HELM_UPGRADE_RC:-0}" ;;
esac
exit 0
"""

STUB_CURL = "exit 0\n"  # admission webhook reports ready immediately
STUB_POWEROFF = 'echo "poweroff $*" >> "$POWEROFF_RECORD"\nexit 0\n'


def _setup(shell, conf_text=CONF):
    charts = shell.tmp / "charts"
    charts.mkdir()
    (charts / "testchart.conf").write_text(conf_text)
    markers = shell.tmp / "markers"
    markers.mkdir()
    upgrade_rec = shell.tmp / "upgrades.txt"
    upgrade_rec.write_text("")
    poweroff_rec = shell.tmp / "poweroff.txt"
    poweroff_rec.write_text("")
    shell.stub("helm", STUB_HELM)
    shell.stub("curl", STUB_CURL)
    shell.stub("poweroff", STUB_POWEROFF)
    env = {
        "CHARTS_DIR": str(charts),
        "MARKERS_DIR": str(markers),
        "LOG_FILE": str(shell.tmp / "log.txt"),
        "RECONCILE_RETRY_DELAY": "0",
        "RECONCILE_ATTEMPTS": "2",
        "HELM_UPGRADE_RECORD": str(upgrade_rec),
        "POWEROFF_RECORD": str(poweroff_rec),
    }
    return charts, markers, upgrade_rec, poweroff_rec, env


def test_release_not_found_powers_off(shell):
    # The fail-closed fix: a measured chart that isn't installed must NOT pass.
    _, _, _, poweroff_rec, env = _setup(shell)
    env["HELM_LIST_STATUS"] = ""  # helm list -> [] (not found)
    res = shell.run(SCRIPT, env=env, require=("bash", "jq"))
    assert res.returncode == 1
    assert "poweroff" in poweroff_rec.read_text()


def test_helm_upgrade_failure_powers_off(shell):
    _, _, upgrade_rec, poweroff_rec, env = _setup(shell)
    env["HELM_LIST_STATUS"] = "deployed"  # release exists, spec changed -> upgrade
    env["HELM_UPGRADE_RC"] = "1"  # upgrade fails every attempt
    res = shell.run(SCRIPT, env=env, require=("bash", "jq"))
    assert res.returncode == 1
    assert "poweroff" in poweroff_rec.read_text()
    # It retried before giving up (RECONCILE_ATTEMPTS=2).
    assert len(upgrade_rec.read_text().strip().splitlines()) == 2


def test_unchanged_spec_skips_without_upgrade_or_poweroff(shell):
    charts, markers, upgrade_rec, poweroff_rec, env = _setup(shell)
    # Pre-seed the marker with the current spec hash so the chart is "unchanged".
    want_hash = hashlib.sha256((charts / "testchart.conf").read_bytes()).hexdigest()
    (markers / "testchart").write_text(want_hash + "\n")
    env["HELM_LIST_STATUS"] = "deployed"
    res = shell.run(SCRIPT, env=env, require=("bash", "jq"))
    assert res.returncode == 0
    assert upgrade_rec.read_text() == ""  # no helm upgrade
    assert poweroff_rec.read_text() == ""  # no poweroff


def test_missing_version_powers_off(shell):
    # A measured conf without a pinned VERSION is a broken spec -> fail closed.
    _, _, _, poweroff_rec, env = _setup(
        shell,
        conf_text='RELEASE="testchart"\nNAMESPACE="testns"\nCHART="repo/testchart"\n',
    )
    env["HELM_LIST_STATUS"] = "deployed"
    res = shell.run(SCRIPT, env=env, require=("bash", "jq"))
    assert res.returncode == 1
    assert "poweroff" in poweroff_rec.read_text()
