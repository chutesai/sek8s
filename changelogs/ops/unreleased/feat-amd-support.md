### Changed

- The `tdx_bootstrap` role is now `tee_bootstrap`: it runs `chutes-cvm host setup`, reboots if
  needed, then verifies with `chutes-cvm host platform --check` and records the result as the
  `host_tee` fact (`tdx` or `snp`). `setup.yml` and `remediate-host.yml` run `pccs_configure`
  only when `host_tee == 'tdx'`, so the same playbooks provision AMD SEV-SNP hosts, which have
  no PCCS.
