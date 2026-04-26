"""Flask API exposing Stage 1 (Lit Review) for the frontend.

Endpoints:
  GET  /health           Liveness ping
  POST /lit-review       Run Stage 1 on a structured hypothesis; return JSON

Dev:
  python -m flask --app app run --debug --port 5000

Or:
  python app.py
"""

from __future__ import annotations

import os
import sys
import traceback
import uuid
import json

from dotenv import load_dotenv

# Load .env BEFORE importing modules that read env at import time.
load_dotenv()

# UTF-8 stdout/stderr so Windows cp1252 doesn't choke on science Unicode.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, jsonify, request  # noqa: E402
from flask_cors import CORS  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.clients import llm  # noqa: E402
from src.lib import plan as plan_lib  # noqa: E402
from src.types import (  # noqa: E402
    Hypothesis,
    StageStatusComplete,
    StageStatusFailed,
    StageStatusRunning,
    StructuredHypothesis,
    now,
)
from lit_review_pipeline import stage  # noqa: E402


app = Flask(__name__)
CORS(app)  # allow cross-origin from the FE dev server / Vercel


def _assistant_mode() -> str:
    """Return assistant mode from env: 'llm' (default) or 'mock'."""
    mode = os.environ.get("ASSISTANT_MODE", "llm").strip().lower()
    return "mock" if mode == "mock" else "llm"


def _mock_assistant_answer(question: str, context: object) -> str:
    """Deterministic mock answers for local UI/dev testing with no API calls."""
    q = question.lower()
    route = ""
    if isinstance(context, dict):
        route = str(context.get("route", "")).strip()

    if "hypothesis" in q or route == "/lab":
        return (
            "A good hypothesis is specific and falsifiable. Try this format: "
            "'If [independent variable] changes in [system], then [dependent variable] "
            "will change because [mechanism].' Next step: define one measurable readout "
            "and one control condition."
        )

    if "novel" in q or "literature" in q or route == "/literature":
        return (
            "For novelty, compare your setup against three closest studies: organism, "
            "intervention range, and readout. If one of those differs materially "
            "(for example a wider concentration range or different endpoint), "
            "you likely have a meaningful angle."
        )

    if "risk" in q or "fail" in q or "wrong" in q:
        return (
            "Top experimental risks to check first: measurement saturation, uncontrolled "
            "baseline drift, and confounded controls. Add one negative control and one "
            "replicate block before scaling."
        )

    if "next" in q or "start" in q or "what can i do" in q:
        return (
            "Recommended next step: write a structured hypothesis, run a quick literature "
            "scan, then draft a minimal protocol with materials, controls, and a success metric."
        )

    return (
        "Mock mode is active, so no external LLM call was made. I can still help you "
        "test the full frontend-backend flow. Ask about hypothesis quality, novelty, risks, "
        "or next experimental steps."
    )


@app.get("/health")
def health():
    """Liveness ping."""
    return jsonify({
        "ok": True,
        "service": "ai-scientist-assistant",
        "stage": "lit_review",
        "model": llm.model_id(),
    })


@app.route("/assistant", methods=["POST"])
def assistant():
    """Answer assistant questions using the configured LLM provider."""
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    context = data.get("context")

    if not question:
        return jsonify({
            "error": "validation_error",
            "detail": "Field 'question' is required and must be a non-empty string.",
        }), 422

    try:
        context_json = json.dumps(context, ensure_ascii=False, default=str)
    except Exception:
        context_json = str(context)

    system_prompt = (
        "You are Praxis, an AI research assistant for experimental science.\n"
        "Give concise, practical guidance grounded in the user's context.\n"
        "If context is incomplete, state assumptions briefly and suggest the best next step.\n"
        "Avoid fabricating citations, results, or data.\n"
        "Respond in plain text."
    )
    user_prompt = (
        f"Route context JSON:\n{context_json}\n\n"
        f"User question:\n{question}"
    )

    if _assistant_mode() == "mock":
        return jsonify({
            "answer": _mock_assistant_answer(question, context),
            "mode": "mock",
        })

    try:
        answer = llm.complete(system_prompt, user_prompt).strip()
        if not answer:
            answer = "I could not generate a response. Please try rephrasing your question."
        return jsonify({"answer": answer, "mode": "llm"})
    except Exception:
        traceback.print_exc()
        return jsonify({
            "error": "assistant_error",
            "detail": "Assistant request failed. Check server logs for the underlying cause.",
        }), 500


@app.post("/lit-review")
def lit_review():
    """Run Stage 1 lit review on a structured hypothesis.

    Request body — either form is accepted:

      Form A (server generates id):
        {
          "structured": {
            "research_question": "...",
            "subject": "...",
            "independent": "...",
            "dependent": "...",
            "conditions": "...",
            "expected": "..."
          },
          "domain": "cell_biology"   // optional
        }

      Form B (client supplies a full Hypothesis):
        {
          "id": "hyp_abc123",
          "structured": { ... },
          "domain": "cell_biology",
          "created_at": "2026-04-26T..."
        }

    Response: LitReviewOutput JSON
        { signal, description, references[], summary, searched_at, tavily_query }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "request_body_required",
                        "detail": "Body must be JSON with a 'structured' field."}), 400

    try:
        if "id" in body:
            hypothesis = Hypothesis(**body)
        else:
            structured = StructuredHypothesis(**(body.get("structured") or {}))
            hypothesis = Hypothesis(
                id=f"hyp_{uuid.uuid4().hex[:12]}",
                structured=structured,
                domain=body.get("domain"),
            )
    except ValidationError as exc:
        # Pydantic gives field-level errors; surface them so FE can highlight.
        return jsonify({"error": "validation_error", "detail": exc.errors()}), 422

    plan = None
    try:
        plan = plan_lib.create_plan(hypothesis, model_id=llm.model_id())
        plan.status["lit_review"] = StageStatusRunning(started_at=now())
        plan_lib.save_plan(plan)

        session = stage.run(plan)

        plan.lit_review = session
        plan.status["lit_review"] = StageStatusComplete(completed_at=now())
        plan_lib.save_plan(plan)

        # Return just the editorial result for the FE card. Full plan is
        # persisted to plans/<id>.json on disk for debugging.
        return jsonify(session.initial_result.model_dump(mode="json"))

    except Exception as exc:
        # Log the full traceback server-side for debugging, but DO NOT leak
        # internal exception details to the client. Raw exception strings
        # can include file paths, library versions, and upstream-service
        # internals that an attacker could use to fingerprint the deployment.
        traceback.print_exc()
        try:
            if plan is not None:
                plan.status["lit_review"] = StageStatusFailed(failed_at=now(), error=str(exc))
                plan_lib.save_plan(plan)
        except Exception:
            pass
        return jsonify({
            "error": "pipeline_error",
            "detail": "Stage 1 failed. Check server logs for the underlying cause.",
        }), 500


if __name__ == "__main__":
    # Flask's app.run() is for local development only. For deployment
    # (Render / Railway / Fly / Cloud Run / etc.), run with a production
    # WSGI server, e.g.:
    #   gunicorn -b 0.0.0.0:5000 app:app
    # FLASK_DEBUG defaults to "0" so dropping this onto a server doesn't
    # accidentally enable the debugger and reloader.
    port = int(os.environ.get("PORT", 8000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
