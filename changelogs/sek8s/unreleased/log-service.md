### Added

- **Chute log shipper agent (`sek8s.log_shipper`, Phase 1).** New headless asyncio package
  (`config`, `crictl`, `checkpoint`, `shipper`, `agent`, `exceptions`) with a `chute-log-shipper`
  console entry, closing the gap where a chute crashing before instance registration left its logs
  unreachable. No new dependencies (reuses `aiohttp` + the `run_command` shell pattern; no k8s API
  access). It discovers chute pods via the CRI socket (`k3s crictl pods -o json`, through a
  restricted wrapper), reads their logs off `/var/log/pods`, and streams them to the validator over
  the per-boot CVM mTLS leaf.
  - **Streaming read path (deterministic memory).** A single coroutine per pod tails only *new*
    bytes from a bounded `buffer_bytes` window (byte offset per log file keyed by inode, so it
    follows kubelet rotation; reset on truncation), rather than re-reading whole files. Memory is
    bounded to `buffer_bytes × pods` (≤ 1 chute pod per GPU); a slow validator pauses reading
    (backpressure); only complete logical lines are shipped (window-cut lines and CRI `P`-runs are
    held); the shipped offset is committed on success and persisted to a `{config_id → {inode →
    offset}}` checkpoint for restart resume. No wall-clock backstop — termination is the validator's
    job (`204`).
  - **Only the `chute` container is captured** (`CONTAINER_NAME`; admission-enforced name);
    init/sidecar containers are skipped so the stream stays single-container and monotonic in `ts`,
    which the validator's high-watermark dedupe relies on.
  - **Wire contract:** `POST https://cvm.chutes.ai/instances/launch_config/{config_id}/logs` with a
    body of `{"deployment_id": "<uuid>", "logs": [{ts, stream, log}]}`. Nothing security-relevant is
    self-asserted — identity is derived validator-side from the mTLS leaf + path + proxy;
    `deployment_id` (from the `chutes/deployment-id` pod label) is the sole top-level field. `204` =
    validator terminated (stop); other 2xx = keep sending; `403`/`404` = rejected (stop + log the
    reason); `413` = payload too large → split the batch and retry the halves (and shrink the batch
    ceiling); any other non-2xx / connection error = transient retry with backoff. No `seq` is sent.
