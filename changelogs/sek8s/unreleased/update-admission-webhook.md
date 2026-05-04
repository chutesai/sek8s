### Fixed

- Mutating webhook no longer applies `automountServiceAccountToken: false` on Pod UPDATE operations, preventing the API server from rejecting immutable-field mutations (e.g. Job controller finalizer sync on completed CronJob pods).
- OPA validating policy (`chutes.rego`) no longer enforces pod-spec rules on Pod UPDATE operations; pod specs are immutable after creation, so spec checks on UPDATE blocked finalizer removal and pod cleanup for pods created before the SA token policy was deployed.
