### Fixed

- OPA validating policy (`chutes.rego`) no longer enforces pod-spec rules on Pod UPDATE operations, preventing the Job controller from being permanently blocked when removing tracking finalizers from completed CronJob pods that predate the `automountServiceAccountToken` policy.
