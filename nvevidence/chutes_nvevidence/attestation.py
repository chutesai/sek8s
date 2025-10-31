from nv_attestation_sdk import attestation


class NvClient:
    def gather_evidence(self, name: str, nonce: str):
        client = attestation.Attestation()
        client.set_name(name)
        client.set_nonce(nonce)
        client.set_claims_version("3.0")

        client.add_verifier(attestation.Devices.GPU, attestation.Environment.REMOTE, "", "")

        evidence = client.get_evidence(options={"ppcie_mode": False})

        return evidence
