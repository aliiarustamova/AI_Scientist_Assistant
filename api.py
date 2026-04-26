from flask import Flask, request, jsonify
from flask_cors import CORS
from planner import generate_experiment_plan
from feedback_store import save_feedback, get_relevant_feedback

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
    
    # Get any relevant feedback
    feedback = get_relevant_feedback(hypothesis)
    
    # Generate the plan
    plan = generate_experiment_plan(hypothesis, feedback=feedback)
    
    return jsonify({
        "success": True,
        "feedback_applied": len(feedback) > 0,
        "plan": plan
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