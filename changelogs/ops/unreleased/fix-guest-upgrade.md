### Fixed
- `drain_and_shutdown.yml`: pass `stdin: "y\n"` to the CLI drain command to satisfy the confirmation prompt introduced in the latest CLI version.
- `shutdown_via_miner.yml`: replace serial-log grep for "Power down" with `is_live_chutes_td.sh` script polling; loop now succeeds when the QEMU guest process exits (`rc != 0`) rather than waiting for a log message that may not appear.
