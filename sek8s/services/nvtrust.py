from nv_attestation_sdk import attestation
import os
import json


client = attestation.Attestation()
client.set_name("thisNode1")
client.set_nonce("931d8dd0add203ac3d8b4fbde75e115278eefcdceac5b87671a748f32364dfcb")
client.set_claims_version("3.0")

print ("[LocalGPUTest] node name :", client.get_name())
file = "../../../policies/local/NVGPULocalv4PolicyExample.json"

client.add_verifier(attestation.Devices.GPU, attestation.Environment.REMOTE, "", "")

print(client.get_verifiers())

print ("[LocalGPUTest] call get_evidence()")
evidence = client.get_evidence(options={"ppcie_mode": False})

# Output to JSON
with open('evidence.json', 'w') as f:
    json.dump(evidence, f, indent=4)

print("Evidence generated in evidence.json")
print(json.dumps(evidence, indent=2))  # Print summary
