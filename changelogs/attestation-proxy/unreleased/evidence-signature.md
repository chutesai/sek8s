### Added
- X-Signature response header on all externally proxied responses. The header contains a base64-encoded RSA-PKCS1v15-SHA256 signature of the response body, signed with the host TLS private key, enabling clients to verify the responder holds the private key corresponding to the TDX-attested certificate.
