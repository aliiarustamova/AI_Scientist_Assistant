"""Flask API for the AI Scientist FE.

Endpoints:
  GET  /health      Liveness ping
  POST /lit-review  Stage 1 (novelty check); persists a plan, returns plan_id
  POST /protocol-sources  protocols.io public search (research_question) + publications fallback
  POST /protocol    Stage 2 (protocol generation); accepts {plan_id} to chain
                    off a prior /lit-review, or {structured} for a fresh start
  POST /materials   Stage 3 (materials roll-up); accepts {plan_id} for chaining
                    or {structured} (runs /protocol internally first)

Dev:
  python -m flask --app app run --debug --port 5000

Or:
  python app.py

Response shape: every Stage 2/3 response carries both `frontend_view`
(the shape the existing React mockup consumes) and the full `raw`
output (rich Pydantic model). Future FE upgrades can switch to `raw`
without a backend change.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
import traceback
import uuid

import requests
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
    ExperimentPlan,
    Hypothesis,
    StageStatusComplete,
    StageStatusFailed,
    StageStatusRunning,
    StructuredHypothesis,
    now,
)
from lit_review_pipeline import stage  # noqa: E402
from protocol_pipeline import stage as protocol_stage  # noqa: E402
from protocol_pipeline.frontend_view import (  # noqa: E402
    adapt_materials,
    adapt_protocol,
)
from protocol_pipeline.sources import parse_draftjs  # noqa: E402


app = Flask(__name__)
CORS(app)  # allow cross-origin from the FE dev server / Vercel

PARSE_HYPOTHESIS_SYSTEM = """You convert raw scientific hypothesis prose into a structured object.

Return ONLY valid JSON with exactly these keys:
- research_question
- subject
- independent
- dependent
- conditions
- expected

Rules:
- Keep values concise and specific.
- Never return null; use empty string if a field is unknown.
- Preserve important scientific notation (units, strain names, temperature, etc.).
- `research_question` should be one sentence ending with '?'.
"""


@app.get("/health")
def health():
    """Liveness ping."""
    return jsonify({
        "ok": True,
        "service": "ai-scientist-assistant",
        "stage": "lit_review",
        "model": llm.model_id(),
    })


@app.post("/parse-hypothesis")
def parse_hypothesis():
    """Parse free-text hypothesis into StructuredHypothesis fields.

    Request:
      { "text": "raw hypothesis prose..." }

    Response:
      { "structured": { research_question, subject, independent, dependent, conditions, expected } }
    """
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({
            "error": "request_body_required",
            "detail": "Body must be JSON with a non-empty 'text' field.",
        }), 400

    try:
        parsed = llm.complete_json(
            PARSE_HYPOTHESIS_SYSTEM,
            f"Hypothesis prose:\n{text}",
            agent_name="parse_hypothesis",
        )
        structured = StructuredHypothesis(**parsed)
        return jsonify({"structured": structured.model_dump(mode="json")})
    except ValidationError as exc:
        return jsonify({"error": "validation_error", "detail": exc.errors()}), 422
    except Exception:
        traceback.print_exc()
        return jsonify({
            "error": "pipeline_error",
            "detail": "Hypothesis parsing failed. Check server logs for the underlying cause.",
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

        # Return the editorial result plus the plan_id so the FE can
        # chain `/protocol` and `/materials` calls against this plan.
        # Full plan is persisted to plans/<plan_id>.json on disk.
        payload = session.initial_result.model_dump(mode="json")
        payload["plan_id"] = plan.id
        return jsonify(payload)

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


# ---------------------------------------------------------------------------
# Stage 2 / 3 helpers
# ---------------------------------------------------------------------------


def _coerce_user_constraints(body: dict) -> str | None:
    """Extract optional researcher constraints; accepts ``user_constraints`` or ``constraints``."""
    raw = body.get("user_constraints")
    if raw is None:
        raw = body.get("constraints")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _merge_user_constraints_onto_plan(plan: ExperimentPlan, body: dict) -> bool:
    """If the request body carries constraints, update ``plan.hypothesis.user_constraints``."""
    if "user_constraints" not in body and "constraints" not in body:
        return False
    uc = _coerce_user_constraints(body)
    h = plan.hypothesis
    if h.user_constraints == uc:
        return False
    plan.hypothesis = h.model_copy(update={"user_constraints": uc})
    return True


def _resolve_plan(body: dict) -> tuple[ExperimentPlan, bool]:
    """Either load an existing plan via `plan_id` or mint a new one from a
    `structured` hypothesis. Returns (plan, is_new). Raises ValueError on
    bad input — caller turns it into a 400/422.

    Both /protocol and /materials accept either form so the FE can chain
    off /lit-review (plan_id) AND a curl-based smoke test can hit them
    without lit-review (structured)."""
    plan_id = body.get("plan_id")
    if plan_id:
        try:
            return plan_lib.load_plan(str(plan_id)), False
        except FileNotFoundError as exc:
            raise ValueError(f"plan_id {plan_id!r} not found on disk") from exc

    if "structured" in body or "id" in body:
        if "id" in body:
            hypothesis = Hypothesis(**body)
        else:
            structured = StructuredHypothesis(**(body.get("structured") or {}))
            hypothesis = Hypothesis(
                id=f"hyp_{uuid.uuid4().hex[:12]}",
                structured=structured,
                domain=body.get("domain"),
                user_constraints=_coerce_user_constraints(body),
            )
        plan = plan_lib.create_plan(hypothesis, model_id=llm.model_id())
        plan_lib.save_plan(plan)
        return plan, True

    raise ValueError("Body must contain either 'plan_id' or 'structured'.")


def _stage_failed_response(stage_name: str, plan: ExperimentPlan | None, exc: Exception):
    """Same pattern as /lit-review: log full traceback server-side, mark
    the stage failed on the plan if we have one, return a sanitized 500."""
    traceback.print_exc()
    try:
        if plan is not None:
            plan.status[stage_name] = StageStatusFailed(failed_at=now(), error=str(exc))
            plan_lib.save_plan(plan)
    except Exception:
        pass
    return jsonify({
        "error": "pipeline_error",
        "detail": f"Stage '{stage_name}' failed. Check server logs for the underlying cause.",
    }), 500


# ---------------------------------------------------------------------------
# POST /protocol
# ---------------------------------------------------------------------------

@app.post("/protocol")
def protocol():
    """Run Stage 2 protocol generation.

    Request body — either form is accepted:

      Form A (chain off /lit-review):
        { "plan_id": "plan_abc...",
          "user_constraints": "e.g. only equipment in lab, time limits"  // optional
        }

      Form B (start fresh; mostly for curl testing):
        {
          "structured": { research_question, subject, independent,
                          dependent, conditions, expected },
          "domain": "cell_biology",   // optional
          "user_constraints": "string"  // optional; same as ``constraints`` alias
        }

    Response:
        {
          "plan_id": "...",
          "frontend_view": FEProtocolView,   // flat steps[], for ExperimentPlan.tsx
          "raw": ProtocolGenerationOutput    // rich shape, for future FE upgrade
        }
    """
    body = request.get_json(silent=True) or {}

    try:
        plan, _is_new = _resolve_plan(body)
    except ValidationError as exc:
        return jsonify({"error": "validation_error", "detail": exc.errors()}), 422
    except ValueError as exc:
        return jsonify({"error": "bad_request", "detail": str(exc)}), 400

    if _merge_user_constraints_onto_plan(plan, body):
        plan.updated_at = now()
    plan_lib.save_plan(plan)

    started = now()
    plan.status["protocol"] = StageStatusRunning(started_at=started)
    plan.updated_at = started
    plan_lib.save_plan(plan)

    try:
        protocol_out, _outline = protocol_stage.run_protocol_only(plan.hypothesis)
    except Exception as exc:
        return _stage_failed_response("protocol", plan, exc)

    completed = now()
    plan.protocol = protocol_out
    plan.status["protocol"] = StageStatusComplete(completed_at=completed)
    plan.updated_at = completed
    plan_lib.save_plan(plan)

    return jsonify({
        "plan_id": plan.id,
        "frontend_view": adapt_protocol(protocol_out).model_dump(mode="json"),
        "raw": protocol_out.model_dump(mode="json"),
    })


# ---------------------------------------------------------------------------
# POST /protocol-sources (protocols.io)
# ---------------------------------------------------------------------------

PROTOCOLS_IO_BASE = "https://www.protocols.io/api/v3"

# protocols.io v3 Search ignores very common English question words; drop for keyword soup.
_STOP_QUERY = frozenset({
    "does", "do", "did", "is", "are", "was", "were", "the", "a", "an", "and", "or", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "be", "have", "has", "had", "not", "no", "can", "will", "would", "this",
    "that", "these", "those", "it", "if", "how", "what", "when", "where", "which", "who", "why", "any",
})


def _pio_auth_headers() -> dict[str, str] | None:
    """Return Authorization header. protocols.io expects ``Bearer <token>`` in most cases."""
    raw = (os.environ.get("PROTOCOLS_IO_API_KEY") or os.environ.get("PROTOCOLS_IO_TOKEN") or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        return {"Authorization": raw}
    return {"Authorization": f"Bearer {raw}"}


def _search_query_candidates(structured: StructuredHypothesis) -> list[str]:
    """Build several search keys from research_question and experimental fields (not RQ only)."""
    rq = (structured.research_question or "").strip()
    extra = " ".join(
        x.strip()
        for x in (
            structured.subject,
            structured.independent,
            structured.dependent,
            structured.conditions,
            structured.expected,
        )
        if isinstance(x, str) and x.strip()
    )
    blob = f"{rq} {extra}".strip()
    alnum_toks = re.findall(r"[A-Za-z][A-Za-z0-9+.\-]*", blob)
    kws = [t for t in alnum_toks if len(t) > 2 and t.lower() not in _STOP_QUERY]
    wlist = rq.split()
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if len(s) < 2 or s in seen:
            return
        if len(s) > 200:
            s = s[:200]
        seen.add(s)
        out.append(s)

    if extra and extra != rq:
        add(extra[:180])
    if kws:
        add(" ".join(kws[:12]))
        add(" ".join(kws[:6]))
    if rq:
        add(rq)
        if wlist:
            add(" ".join(wlist[:8]))
            add(" ".join(wlist[:4]))
            add(max(wlist, key=len))
    if not out and extra:
        add(extra)
    return out[:14]


def _pio_items_from_response(data: object) -> list[dict] | None:
    """Return item list if the payload looks like a successful hit."""
    if not isinstance(data, dict):
        return None
    sc = data.get("status_code")
    if sc is not None:
        try:
            if int(sc) != 0:
                return None
        except (TypeError, ValueError):
            return None
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    return [x for x in items if isinstance(x, dict)]


def _normalize_pio_doi(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "dx.doi.org/",
    ):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix) :]
            break
    s = s.strip().lstrip("/")
    if s.startswith("10."):
        return s
    return None


def _pio_author_cite(item: dict) -> str | None:
    names: list[str] = []
    authors = item.get("authors")
    if isinstance(authors, list):
        for a in authors:
            if isinstance(a, dict):
                n = a.get("name")
                if isinstance(n, str) and n.strip():
                    names.append(n.strip())
    if not names:
        c = item.get("creator")
        if isinstance(c, dict):
            n = c.get("name")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


def _pio_year(item: dict) -> str | None:
    for key in ("published_on", "created_on"):
        ts = item.get(key)
        if ts is None:
            continue
        try:
            return str(datetime.fromtimestamp(int(ts), tz=timezone.utc).year)
        except (ValueError, TypeError, OSError, OverflowError):
            continue
    return None


def _build_pio_citation(item: dict, pio_url: str | None) -> str:
    """One-line attribution for the card (authors, year, title, venue, DOI or URL)."""
    author = _pio_author_cite(item)
    doi = _normalize_pio_doi(item.get("doi"))
    year = _pio_year(item)
    title = item.get("title")
    title_s = (title.strip() if isinstance(title, str) else "") or ""
    if len(title_s) > 90:
        title_s = title_s[:87] + "…"

    head_parts: list[str] = []
    if author:
        head_parts.append(author)
    if year:
        head_parts.append(f"({year})")
    if title_s:
        head_parts.append(f"“{title_s}”")

    head = " ".join(head_parts) if head_parts else ""

    if doi:
        tail = f"protocols.io. https://doi.org/{doi}"
    elif pio_url:
        tail = f"protocols.io. {pio_url}"
    else:
        tail = "protocols.io"

    if head:
        return f"{head}. {tail}"
    return tail


def normalize_protocols(data: dict, fetch_mode: str) -> dict:
    """Map protocols.io `items` to FE card rows."""
    protocols: list[dict] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        if item.get("id") is None:
            continue
        # protocols.io description is often Draft.js JSON (string or dict); use shared parser.
        raw_text = parse_draftjs(item.get("description"))
        text = " ".join((raw_text or "").split())
        summary = text[:300] + ("..." if len(text) > 300 else "")
        raw_u = item.get("url")
        pio_url = raw_u.strip() if isinstance(raw_u, str) else None
        if not pio_url and item.get("uri"):
            pio_url = f"https://www.protocols.io/view/{item.get('uri')}"
        row: dict = {
            "id": str(item.get("id")),
            "title": item.get("title") or "Untitled protocol",
            "source": "protocols.io",
            "summary": summary,
            "keySteps": [],
            "citation": _build_pio_citation(item, pio_url),
        }
        if pio_url:
            row["url"] = pio_url
        protocols.append(row)
    return {
        "sources": protocols,
        "fetch_mode": fetch_mode,
    }


def fetch_protocols_from_protocols_io(
    structured: StructuredHypothesis,
    *,
    search_key: str | None = None,
) -> tuple[list[dict], str]:
    """GET protocols.io public search, then latest publications. Debug-print each request."""
    headers = _pio_auth_headers()
    if not headers:
        return [], "missing_credentials"

    unique_candidates = _search_query_candidates(structured)
    if search_key and str(search_key).strip():
        sk = str(search_key).strip()[:200]
        if sk not in unique_candidates:
            unique_candidates.insert(0, sk)

    search_url = f"{PROTOCOLS_IO_BASE}/protocols"
    base_search_params = {
        "filter": "public",
        "order_field": "activity",
        "order_dir": "desc",
        "page_size": "20",
        "page_id": "1",
    }
    for q in unique_candidates:
        req_params = {**base_search_params, "key": q}
        try:
            res = requests.get(search_url, headers=headers, params=req_params, timeout=45)
        except requests.RequestException as exc:
            print("QUERY:", q, "REQUEST_ERR:", exc)
            continue
        print("QUERY:", q)
        print("STATUS:", res.status_code)
        print("RAW RESPONSE:", res.text[:500])
        try:
            data = res.json()
        except json.JSONDecodeError:
            continue
        items = _pio_items_from_response(data)
        if items:
            out = normalize_protocols({"items": items}, fetch_mode="search")
            return out["sources"], out["fetch_mode"]

    pub_url = f"{PROTOCOLS_IO_BASE}/publications"
    pub_params = {"latest": 20}
    try:
        res = requests.get(pub_url, headers=headers, params=pub_params, timeout=45)
    except requests.RequestException as exc:
        print("QUERY:", "publications_fallback", pub_params, "REQUEST_ERR:", exc)
        return [], "empty"
    print("QUERY:", "publications_fallback", pub_params)
    print("STATUS:", res.status_code)
    print("RAW RESPONSE:", res.text[:500])
    try:
        data = res.json()
    except json.JSONDecodeError:
        return [], "empty"
    items = _pio_items_from_response(data)
    if items:
        out = normalize_protocols({"items": items}, fetch_mode="publications_fallback")
        return out["sources"], out["fetch_mode"]
    return [], "empty"


@app.post("/protocol-sources")
def protocol_sources():
    """Return normalized protocols.io publications for the Protocol Sources step.

    Request:
      { "structured": { research_question, subject, ... } }

    Response:
      { "sources": [ { id, title, source, summary, keySteps, citation, url? } ] }
    """
    body = request.get_json(silent=True) or {}
    raw_structured = body.get("structured")
    if not isinstance(raw_structured, dict):
        raw_structured = {
            "research_question": "",
            "subject": "",
            "independent": "",
            "dependent": "",
            "conditions": "",
            "expected": "",
        }
    try:
        structured = StructuredHypothesis(**raw_structured)
    except ValidationError as exc:
        return jsonify({"error": "validation_error", "detail": exc.errors()}), 422

    search_key = (structured.research_question or "").strip()
    fetch_mode = "empty"
    try:
        sources, fetch_mode = fetch_protocols_from_protocols_io(
            structured,
            search_key=search_key,
        )
    except Exception:  # defensive; fetch helper already swallows, but do not 500
        traceback.print_exc()
        sources = []
        fetch_mode = "error"
    return jsonify(
        {
            "sources": sources,
            "search_query": search_key,
            "fetch_mode": fetch_mode,
        },
    )


# ---------------------------------------------------------------------------
# POST /materials
# ---------------------------------------------------------------------------

@app.post("/materials")
def materials():
    """Run Stage 3 materials roll-up.

    Request body — either form is accepted:

      Form A (chain off /protocol):
        { "plan_id": "plan_abc..." }
        — requires the plan to already have a populated `protocol` field.
          If it doesn't, returns 400 telling the FE to call /protocol first.

      Form B (start fresh): same shape as /protocol Form B. Internally
        runs /protocol first, then the roll-up. Slow (~50-70s) but
        convenient for one-shot curl testing.

    Response:
        {
          "plan_id": "...",
          "frontend_view": FEMaterialsView,   // grouped, for ExperimentPlan.tsx
          "raw": MaterialsOutput              // flat shape, for future upgrade
        }
    """
    body = request.get_json(silent=True) or {}

    try:
        plan, is_new = _resolve_plan(body)
    except ValidationError as exc:
        return jsonify({"error": "validation_error", "detail": exc.errors()}), 422
    except ValueError as exc:
        return jsonify({"error": "bad_request", "detail": str(exc)}), 400

    # If we got a plan_id whose protocol stage hasn't run yet, surface that
    # explicitly rather than silently re-running it. The FE should call
    # /protocol first; chaining is sequential by design.
    if not is_new and plan.protocol is None:
        return jsonify({
            "error": "protocol_not_run",
            "detail": "This plan has no protocol yet. POST /protocol first, then retry /materials.",
        }), 400

    # Form B: brand-new plan with no protocol yet — run /protocol implicitly.
    if plan.protocol is None:
        started = now()
        plan.status["protocol"] = StageStatusRunning(started_at=started)
        plan.updated_at = started
        plan_lib.save_plan(plan)
        try:
            protocol_out, _outline = protocol_stage.run_protocol_only(plan.hypothesis)
        except Exception as exc:
            return _stage_failed_response("protocol", plan, exc)
        completed = now()
        plan.protocol = protocol_out
        plan.status["protocol"] = StageStatusComplete(completed_at=completed)
        plan.updated_at = completed
        plan_lib.save_plan(plan)

    started = now()
    plan.status["materials"] = StageStatusRunning(started_at=started)
    plan.updated_at = started
    plan_lib.save_plan(plan)

    try:
        materials_out = protocol_stage.run_materials_only(plan.protocol)
    except Exception as exc:
        return _stage_failed_response("materials", plan, exc)

    completed = now()
    plan.materials = materials_out
    plan.status["materials"] = StageStatusComplete(completed_at=completed)
    plan.updated_at = completed
    plan_lib.save_plan(plan)

    return jsonify({
        "plan_id": plan.id,
        # Pass the protocol so adapt_materials populates `used_in_steps`
        # cross-links from each material to the steps that reference it.
        "frontend_view": adapt_materials(materials_out, protocol=plan.protocol).model_dump(mode="json"),
        "raw": materials_out.model_dump(mode="json"),
    })


if __name__ == "__main__":
    # Flask's app.run() is for local development only. For deployment
    # (Render / Railway / Fly / Cloud Run / etc.), run with a production
    # WSGI server, e.g.:
    #   gunicorn -b 0.0.0.0:5000 app:app
    # FLASK_DEBUG defaults to "0" so dropping this onto a server doesn't
    # accidentally enable the debugger and reloader.
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
