### Changed
- **The guest-image build's measurement step now sources known host classes from the API.**
  `chutes-cvm measurements generate` reads the published host profiles (and their fingerprints)
  from the control plane instead of an in-repo baseline registry, so the build host must reach the
  API. The `chutes-miner-vm` build passes `--api-base` (var `measurements_api_base`, default
  `https://api.chutes.ai`); override it for an isolated build environment. It also passes
  `--include-pending` so the build (the authoritative generator) processes host classes awaiting
  generation — turning newly submitted profiles into published measurements — where a third-party
  verification run would see measured classes only. The GPU-VM build's
  `measurements generate --register rtmr3` step is unaffected (RTMR3 is image-only, no API call).
