import json
import os

FEEDBACK_FILE = "feedback.json"

def load_feedback() -> list:
    if not os.path.exists(FEEDBACK_FILE):
        return []
    with open(FEEDBACK_FILE, "r") as f:
        return json.load(f)

def save_feedback(experiment_type: str, section: str, correction: str):
    feedback = load_feedback()
    feedback.append({
        "experiment_type": experiment_type,
        "section": section,
        "correction": correction
    })
    with open(FEEDBACK_FILE, "w") as f:
        json.dump(feedback, f, indent=2)
    print(f"Feedback saved.")

def get_relevant_feedback(hypothesis: str) -> list:
    feedback = load_feedback()
    if not feedback:
        return []
    
    hypothesis_lower = hypothesis.lower()
    relevant = []
    
    for entry in feedback:
        keywords = entry["experiment_type"].lower().split()
        if any(word in hypothesis_lower for word in keywords):
            relevant.append(f"[{entry['section']}] {entry['correction']}")
    
    return relevant