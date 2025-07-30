from typing import Dict, Tuple
from sek8s.opa import OPAPolicyEngine

class AdmissionController:
    def __init__(self):
        self.policy_engine = OPAPolicyEngine()
        
    def validate_request(self, admission_review: Dict) -> Tuple[bool, Dict]:
        """Main admission validation logic"""
        request = admission_review.get("request", {})
        
        # Evaluate against OPA policies
        violations = self.policy_engine.evaluate_policies(request)
        # violations = []
        
        allowed = len(violations) == 0
        
        response = {
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview",
            "response": {
                "uid": request.get("uid"),
                "allowed": allowed,
                "status": {
                    "message": "; ".join(violations) if violations else "Allowed"
                }
            }
        }
        
        return allowed, response