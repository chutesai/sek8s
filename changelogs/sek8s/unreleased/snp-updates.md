### Fixed

- Quote nonce validation (`sek8s.nonce`) requires exactly 64 hex characters. It used
  `bytes.fromhex`, which skips whitespace between byte pairs, so a 64-character nonce with a
  gap decoded short of 32 bytes (refused downstream regardless).
