### Fixed

- Bridge networking now clamps TCP MSS to the egress interface's PMTU (`setup-bridge.sh`),
  so guest TLS handshakes survive a small-MTU `public_interface` such as a WireGuard tunnel
  (MTU 1280). Previously the guest advertised an MSS from its own 1500 NIC, and oversized
  handshake segments were black-holed on the smaller uplink — connections established but
  stalled during the TLS handshake.
