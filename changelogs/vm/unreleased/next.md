### Changed

- OPA per-decision logging is now off by default. `opa-config.yaml` hardcoded
  `decision_logs.console: true`, which wrote the full AdmissionReview input (complete pod specs) to the
  journal for every admitted object — high-volume noise that evicted boot/attestation history from the
  journal window, and tenant workload detail in a log the miner can read over the status API. The config
  and unit are now templated, so the existing `opa_decision_logs` and `opa_log_level` variables are live
  rather than dead; debug builds can opt back in with `-e opa_decision_logs=true`.
