### Changed

- `chutes.rego` now documents why its rules are namespace-scoped. Admission policy is two
  tier: container-escape primitives (`pods.rego`, `volumes.rego` — privileged, host
  namespaces, dangerous capabilities, hostPath, forbidden env) are enforced in **every**
  namespace, while the chute workload-shape rules in `chutes.rego` (non-root, registry
  allowlist, `chutes/config-id` label, container named `chute`, `chutes run` command) are
  gated on `namespace == "chutes"` by design. Outside that namespace the boundary is RBAC,
  not admission — the miner's ClusterRole is get/list/watch only. Without this stated, a
  reviewer noticing that `runAsUser: 0` is admitted in e.g. `monitoring` could "fix" it by
  dropping the namespace guard and reject our own system workloads.

  **No rule changed, but `/etc/opa/policies` is measured into RTMR3, so this comment moves
  the measurement.** Regenerate expected-measurement baselines before rollout.
