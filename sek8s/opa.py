import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Dict

from sek8s.config import OPAEngineSettings


class OPAPolicyEngine:
    def __init__(self):
        self.settings = OPAEngineSettings()
        self.policy_dir = Path(self.settings.policy_dir)
        self.opa_binary = "./bin/opa"  # Ensure OPA binary is installed
        
    def evaluate_policies(self, admission_request: Dict[str, Any]) -> list[str]:
        """Evaluate admission request against all policies"""
        violations = []
        
        # Create input JSON for OPA
        opa_input = {
            "request": admission_request,
            "allowed_registries": self.settings.allowed_registries
        }
        
        # Evaluate each policy file
        for policy_file in self.policy_dir.glob("*.rego"):
            try:
                result = self._evaluate_single_policy(policy_file, opa_input)
                if result and result.get("result"):
                    # Extract violations from OPA result
                    for _result in result["result"]:
                        for expression in _result['expressions']:
                            for violation in expression['value']:
                                if isinstance(violation, dict) and "msg" in violation:
                                    violations.append(violation["msg"])
                                elif isinstance(violation, str):
                                    violations.append(violation)
            except Exception as e:
                print(f"Error evaluating policy {policy_file}: {e}")
                # Fail secure - treat policy evaluation errors as violations
                violations.append(f"Policy evaluation error in {policy_file.name}")
        
        return violations
    
    def _evaluate_single_policy(self, policy_file: Path, opa_input: Dict) -> Dict:
        """Evaluate a single policy file against input"""
        
        # Create temporary file for input
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(opa_input, f)
            input_file = f.name
        
        try:
            # Run OPA evaluation
            cmd = [
                self.opa_binary,
                "eval",
                "-d", str(policy_file.absolute()),  # Policy file
                "-i", input_file,        # Input file
                "-f", "json",            # Output format
                "data.admission.deny"    # Query
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,  # Prevent hanging
                check=False
            )
            
            if result.returncode == 0:
                return json.loads(result.stdout)
            else:
                print(f"OPA evaluation failed: {result.stderr}")
                return {"result": [f"Policy evaluation failed: {result.stderr}"]}
                
        finally:
            # Clean up temp file
            os.unlink(input_file)
