### Fixed
- Fix LUKS key confirmation on first boot: freshly provisioned volumes now set the KEY_ADDED flag so confirm_rotation sends rotated=true, preventing the API from discarding the applied passphrase and bricking the volume on subsequent boots
