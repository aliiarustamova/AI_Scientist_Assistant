from flask import Flask, request, jsonify
from flask_cors import CORS
from planner import generate_experiment_plan
from feedback_store import save_feedback, get_relevant_feedback
from protocols_client import get_protocol_bundle, search_protocols

app = Flask(__name__)
CORS(app)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    
    if not data or "hypothesis" not in data:
        return jsonify({"error": "hypothesis is required"}), 400
    
    hypothesis = data["hypothesis"]
    selected_protocol_id = data.get("selected_protocol_id")
    
    # Get any relevant feedback
    feedback = get_relevant_feedback(hypothesis)
    
    # Get protocol context (optional grounding)
    protocol_bundle = get_protocol_bundle(hypothesis, selected_protocol_id)
    
    # Determine if protocol grounding was successful
    protocol_grounded = protocol_bundle.get("grounding_status") == "success"
    
    # Generate the plan with protocol context
    protocol_context = None
    if protocol_grounded:
        protocol_context = protocol_bundle
    
    plan = generate_experiment_plan(hypothesis, feedback=feedback, protocol_context=protocol_context)
    
    # Check if plan generation failed
    if "error" in plan:
        return jsonify({
            "success": False,
            "error": plan.get("error"),
            "details": plan.get("details")
        }), 500
    
    return jsonify({
        "success": True,
        "feedback_applied": len(feedback) > 0,
        "protocol_grounded": protocol_grounded,
        "protocol_search_status": protocol_bundle.get("grounding_status"),
        "selected_protocol": protocol_bundle.get("selected_protocol"),
        "protocol_candidates": protocol_bundle.get("candidates"),
        "protocol_gaps": protocol_bundle.get("gaps"),
        "plan": plan
    })

@app.route("/protocols/search", methods=["POST"])
def protocols_search():
    """Search for protocols (supports future UI workflow)."""
    data = request.get_json()
    
    if not data or "query" not in data:
        return jsonify({"success": False, "error": "query is required"}), 400
    
    query = data["query"]
    candidates = search_protocols(query, limit=5)
    
    if not candidates:
        return jsonify({
            "success": True,
            "protocol_candidates": [],
            "message": "No protocols found or token not configured"
        })
    
    return jsonify({
        "success": True,
        "protocol_candidates": candidates
    })

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "data is required"}), 400
    
    required = ["experiment_type", "section", "correction"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"{field} is required"}), 400
    
    save_feedback(
        experiment_type=data["experiment_type"],
        section=data["section"],
        correction=data["correction"]
    )
    
    return jsonify({"success": True, "message": "Feedback saved"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)