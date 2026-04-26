import json
from claude_client import client, MODEL
from prompts import EXPERIMENT_PLAN_PROMPT, FEEDBACK_CONTEXT_TEMPLATE, PROTOCOL_CONTEXT_TEMPLATE

def generate_experiment_plan(hypothesis: str, feedback: list = None, protocol_context: dict = None) -> dict:
    
    feedback_context = ""
    if feedback:
        feedback_text = "\n".join([f"- {f}" for f in feedback])
        feedback_context = FEEDBACK_CONTEXT_TEMPLATE.format(feedback=feedback_text)
    
    # Format protocol context
    protocol_context_str = "{}"
    if protocol_context:
        try:
            protocol_context_str = json.dumps(protocol_context)
        except Exception:
            protocol_context_str = '{"error": "Failed to serialize protocol context"}'
    else:
        protocol_context_str = "No protocol context available."
    
    protocol_context_formatted = PROTOCOL_CONTEXT_TEMPLATE.format(
        protocol_context=protocol_context_str
    )
    
    prompt = EXPERIMENT_PLAN_PROMPT.format(
        hypothesis=hypothesis,
        feedback_context=feedback_context,
        protocol_context=protocol_context_formatted
    )
    
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        raw = response.content[0].text
        
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    
    except json.JSONDecodeError as e:
        return {
            "error": "Failed to parse generated plan",
            "parse_error": str(e),
            "raw_response": raw[:500] if 'raw' in locals() else ""
        }
    except Exception as e:
        return {
            "error": "Failed to generate experiment plan",
            "details": str(e)
        }
