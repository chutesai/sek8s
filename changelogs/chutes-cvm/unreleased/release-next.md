### Changed

- **`chutes-cvm host verify` no longer requires a downloaded base image.** Verification asks a
  version-free question — *is this host class known, and which published images cover it?* — via the
  new `POST /servers/tdx/host_profiles/status`, replacing the per-version
  `POST /servers/tdx/preflight` call. Previously a host with no image set reported
  `BLOCKED (image): manifest.json missing`, which made the gate unreachable on exactly the hosts
  that need it: a brand-new box is verified (and `--submit`-registered for measurement) *before* it
  downloads anything. The two gates are now cleanly split — `host verify` answers "can this host run
  anything, and what", `guest launch` keeps its unchanged per-version preflight against the
  `(version, rc)` it actually holds.
  - READY now lists the images the class can launch, so an operator sees what to download.
  - A class that is registered but awaiting measurement generation is reported as such and is no
    longer prompted to re-submit — re-submitting does not advance the queue.
  - If a base image *is* downloaded, its `(version, rc)` is checked against the covered set as a
    **note**: an uncovered image is flagged (exit 2) but never invalidates the host-class verdict.
    `--base-image` now selects the image for that note only.
