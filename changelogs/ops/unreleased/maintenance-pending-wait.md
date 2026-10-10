### Changed

- The upgrade playbooks (`upgrade-guest.yml`, `upgrade-host.yml`, `remediate-host.yml`) follow the
  two-step maintenance flow: after requesting maintenance they wait while the server is `pending`
  (sole-survivor instances still serving while the validator posts replacement bounties) and only
  drain and shut the guest down once it reaches `maintenance`. `start-maintenance` exits 0 on
  `pending`, so the drain used to start immediately and fail after `upgrade_drain_timeout_seconds`
  with the survivors' pods still running. The wait is bounded by `upgrade_pending_timeout_seconds`
  (default 11700, the validator's 3-hour deadline plus margin); if it runs out, the play stops
  without shutting the guest down. A re-run against a `pending` server no longer requests
  maintenance again. Requires chutes-miner CLI 0.9.0 or later.
