
import logging
from flask import Flask, jsonify, request

from sek8s.admission.admission_controller import AdmissionController
from sek8s.config import AdmissionSettings


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize admission controller
controller = AdmissionController()

@app.route('/validate', methods=['POST'])
def validate():
    """Admission webhook endpoint"""
    try:
        admission_review = request.get_json()
        
        if not admission_review:
            return jsonify({"error": "Invalid request"}), 400
        
        allowed, response = controller.validate_admission(admission_review)
        return jsonify(response)
        
    except Exception as e:
        logging.error(f"Admission validation error: {e}")
        
        # Fail secure - deny admission on errors
        return jsonify({
            "apiVersion": "admission.k8s.io/v1",
            "kind": "AdmissionReview", 
            "response": {
                "uid": request.get_json().get("request", {}).get("uid"),
                "allowed": False,
                "status": {"message": f"Internal error: {str(e)}"}
            }
        })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"})

def run():
    settings = AdmissionSettings()
    app.run(host='localhost', port=settings.controller_port, debug=settings.debug)

if __name__ == '__main__':
    run()