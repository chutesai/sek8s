"""Unit tests for the automountServiceAccountToken mutation logic."""

from sek8s.services.admission_controller import AdmissionWebhookServer

# ---------------------------------------------------------------------------
# Helper to build minimal admission request dicts
# ---------------------------------------------------------------------------


def _pod_req(labels: dict, image: str, automount=None) -> dict:
    spec: dict = {"containers": [{"name": "c", "image": image}]}
    if automount is not None:
        spec["automountServiceAccountToken"] = automount
    return {
        "kind": {"kind": "Pod"},
        "object": {"metadata": {"labels": labels}, "spec": spec},
    }


def _deployment_req(labels: dict, image: str, automount=None) -> dict:
    spec: dict = {"containers": [{"name": "c", "image": image}]}
    if automount is not None:
        spec["automountServiceAccountToken"] = automount
    return {
        "kind": {"kind": "Deployment"},
        "object": {
            "metadata": {"name": "test"},
            "spec": {"template": {"metadata": {"labels": labels}, "spec": spec}},
        },
    }


def _job_req(labels: dict, image: str, automount=None) -> dict:
    spec: dict = {"containers": [{"name": "c", "image": image}]}
    if automount is not None:
        spec["automountServiceAccountToken"] = automount
    return {
        "kind": {"kind": "Job"},
        "object": {
            "metadata": {"labels": labels},
            "spec": {"template": {"metadata": {"labels": labels}, "spec": spec}},
        },
    }


# ---------------------------------------------------------------------------
# Agent exemption: no patch
# ---------------------------------------------------------------------------

AGENT_LABELS = {"app.kubernetes.io/name": "agent"}
AGENT_IMAGE = "parachutes/chutes-agent:k3s-latest"


class TestAgentExemption:
    def test_agent_pod_not_patched(self):
        req = _pod_req(AGENT_LABELS, AGENT_IMAGE, automount=True)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []

    def test_agent_pod_without_automount_not_patched(self):
        req = _pod_req(AGENT_LABELS, AGENT_IMAGE)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []

    def test_agent_deployment_not_patched(self):
        req = _deployment_req(AGENT_LABELS, AGENT_IMAGE, automount=True)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []

    def test_agent_deployment_without_automount_not_patched(self):
        req = _deployment_req(AGENT_LABELS, AGENT_IMAGE)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []

    def test_agent_digest_image_not_patched(self):
        image = "parachutes/chutes-agent@sha256:abcdef1234567890"
        req = _pod_req(AGENT_LABELS, image, automount=True)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []


# ---------------------------------------------------------------------------
# Non-agent workloads: must be patched
# ---------------------------------------------------------------------------


class TestNonAgentPatched:
    def test_regular_pod_patched(self):
        req = _pod_req({"app": "web"}, "nginx:latest")
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1
        assert patches[0]["path"] == "/spec/automountServiceAccountToken"
        assert patches[0]["value"] is False

    def test_regular_pod_with_automount_true_patched(self):
        req = _pod_req({"app": "web"}, "nginx:latest", automount=True)
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1

    def test_regular_pod_with_automount_false_not_patched(self):
        req = _pod_req({"app": "web"}, "nginx:latest", automount=False)
        assert AdmissionWebhookServer._build_sa_token_patches(req) == []

    def test_regular_deployment_patched(self):
        req = _deployment_req({"app": "web"}, "nginx:latest")
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1
        assert patches[0]["path"] == "/spec/template/spec/automountServiceAccountToken"

    def test_job_patched(self):
        req = _job_req({"app": "worker"}, "busybox:latest")
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1
        assert patches[0]["path"] == "/spec/template/spec/automountServiceAccountToken"


# ---------------------------------------------------------------------------
# Abuse prevention: wrong label or wrong image still patched
# ---------------------------------------------------------------------------


class TestAbusePrevention:
    def test_agent_label_wrong_image_patched(self):
        req = _pod_req(AGENT_LABELS, "evil/agent:latest")
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1

    def test_agent_image_wrong_label_patched(self):
        req = _pod_req({"app.kubernetes.io/name": "not-agent"}, AGENT_IMAGE)
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1

    def test_agent_label_wrong_image_deployment_patched(self):
        req = _deployment_req(AGENT_LABELS, "evil/agent:latest")
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1

    def test_job_with_agent_label_and_image_patched(self):
        """Jobs are never exempt, even with agent label + image."""
        req = _job_req(AGENT_LABELS, AGENT_IMAGE)
        patches = AdmissionWebhookServer._build_sa_token_patches(req)
        assert len(patches) == 1
