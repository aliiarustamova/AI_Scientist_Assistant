"""Tests for the Flask API exposing Stage 1.

Uses Flask's test_client (no live server). The Stage 1 runner is monkey-
patched so we exercise routing / validation / response shape without
burning Tavily or LLM credits.

Run:
  pytest tests/test_api.py -v
"""

from __future__ import annotations

import pytest

import app as flask_app
from src.types import (
    Citation,
    LitReviewOutput,
    LitReviewSession,
    StageStatusComplete,
)


@pytest.fixture
def client(monkeypatch):
    # Stub the Stage 1 runner so the endpoint test is hermetic.
    def _fake_run(plan):
        ref = Citation(
            source="europe_pmc",
            confidence="high",
            title="A mock paper",
            authors=["Alice Doe", "Bob Roe"],
            year=2024,
            venue="Mock Journal",
            doi="10.0000/mock.2024",
            url="https://doi.org/10.0000/mock.2024",
            snippet="Mock abstract.",
            relevance_score=0.9,
            matched_on=["mock", "test"],
            description="Neutral mock description.",
            importance="Why this would match the user's hypothesis.",
        )
        out = LitReviewOutput(
            signal="novel",
            description="Top-level mock signal explanation.",
            references=[ref],
            searched_at="2026-04-26T00:00:00+00:00",
            tavily_query="mock query",
            summary="One-sentence mock summary.",
        )
        return LitReviewSession(
            id="lr_mock",
            hypothesis_id=plan.hypothesis.id,
            initial_result=out,
            chat_history=[],
            cached_search_context="{}",
            user_decision="pending",
        )

    monkeypatch.setattr(flask_app.stage, "run", _fake_run)
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


# =============================================================================
# /health
# =============================================================================

def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["stage"] == "lit_review"


# =============================================================================
# /lit-review — happy paths
# =============================================================================

def _valid_body():
    return {
        "structured": {
            "research_question": "Does X improve Y?",
            "subject": "Subject organism",
            "independent": "Variable A",
            "dependent": "Variable B",
            "conditions": "Standard lab conditions",
            "expected": "Y increases by 30%",
        },
        "domain": "cell_biology",
    }


def test_lit_review_returns_200_with_valid_output(client):
    r = client.post("/lit-review", json=_valid_body())
    assert r.status_code == 200
    body = r.get_json()
    assert body["signal"] == "novel"
    assert isinstance(body["references"], list)
    assert body["references"][0]["title"] == "A mock paper"


def test_lit_review_response_includes_summary(client):
    r = client.post("/lit-review", json=_valid_body())
    body = r.get_json()
    assert body["summary"]
    assert body["description"]


def test_lit_review_accepts_explicit_id_form(client):
    body = _valid_body()
    body["id"] = "hyp_clientside_123"
    body["created_at"] = "2026-04-26T00:00:00+00:00"
    r = client.post("/lit-review", json=body)
    assert r.status_code == 200


# =============================================================================
# /lit-review — error paths
# =============================================================================

def test_missing_body_returns_400(client):
    r = client.post("/lit-review")
    assert r.status_code == 400
    assert r.get_json()["error"] == "request_body_required"


def test_missing_structured_field_returns_422(client):
    """Empty 'structured' object should fail Pydantic validation."""
    r = client.post("/lit-review", json={"structured": {}})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "validation_error"
    assert isinstance(body["detail"], list)


def test_pipeline_error_returns_500(client, monkeypatch):
    """If the runner raises, the endpoint surfaces a 500 — and crucially
    does NOT leak the raw exception string into the client response."""
    def _broken(plan):
        raise RuntimeError("upstream blew up with secret/path/info")
    monkeypatch.setattr(flask_app.stage, "run", _broken)

    r = client.post("/lit-review", json=_valid_body())
    assert r.status_code == 500
    body = r.get_json()
    assert body["error"] == "pipeline_error"
    # Internal exception detail must not appear in the response (security).
    assert "secret/path/info" not in body["detail"]
    assert "upstream blew up" not in body["detail"]


def test_create_plan_failure_returns_500_cleanly(client, monkeypatch):
    """If plan creation itself raises (before the runner is reached), the
    except handler must NOT explode trying to mark a non-existent plan as
    failed. Exercises the `if plan is not None` guard. Also verifies
    internal error details don't leak into the response."""
    def _broken_create(hypothesis, model_id):
        raise RuntimeError("disk full at /var/lib/secret-path")
    monkeypatch.setattr(flask_app.plan_lib, "create_plan", _broken_create)

    r = client.post("/lit-review", json=_valid_body())
    assert r.status_code == 500
    body = r.get_json()
    assert body["error"] == "pipeline_error"
    assert "/var/lib/secret-path" not in body["detail"]


def test_lit_review_response_includes_plan_id(client):
    """The FE chains `/protocol` and `/materials` against the plan_id, so
    /lit-review must return one. Without this, the chain is impossible."""
    r = client.post("/lit-review", json=_valid_body())
    body = r.get_json()
    assert "plan_id" in body
    assert body["plan_id"].startswith("plan_")


# =============================================================================
# /protocol — Stage 2 endpoint
# =============================================================================
#
# These tests stub `protocol_pipeline.stage.run_protocol_only` and
# `run_materials_only` so endpoint shape / chaining / error paths can be
# exercised without spending LLM credits.

from src.types import (  # noqa: E402  (test-only late import)
    Material,
    MaterialsOutput,
    Procedure,
    ProtocolGenerationOutput,
    ProtocolStep,
)


def _stub_protocol_output() -> ProtocolGenerationOutput:
    return ProtocolGenerationOutput(
        experiment_type="mock-experiment",
        domain="cell_biology",
        procedures=[Procedure(
            name="Mock Cell Culture Preparation", intent="prep cells",
            steps=[ProtocolStep(n=1, title="Seed", body_md="Seed 1e6 cells per flask.")],
        )],
        steps=[ProtocolStep(n=1, title="Seed", body_md="Seed 1e6 cells per flask.")],
        total_steps=1,
    )


def _stub_materials_output() -> MaterialsOutput:
    return MaterialsOutput(
        materials=[Material(id="mat_1", name="DMEM", category="reagent")],
        total_unique_items=1,
        by_category={"reagent": 1},
    )


@pytest.fixture
def protocol_client(client, monkeypatch):
    """Adds protocol-stage stubs on top of the lit-review client."""
    def _fake_run_protocol(hypothesis, **kwargs):
        return _stub_protocol_output(), None  # (output, outline)
    def _fake_run_materials(protocol):
        return _stub_materials_output()
    monkeypatch.setattr(flask_app.protocol_stage, "run_protocol_only", _fake_run_protocol)
    monkeypatch.setattr(flask_app.protocol_stage, "run_materials_only", _fake_run_materials)
    return client


def test_protocol_with_structured_form_returns_200(protocol_client):
    """Form B (start fresh): pass a structured hypothesis directly."""
    r = protocol_client.post("/protocol", json=_valid_body())
    assert r.status_code == 200
    body = r.get_json()
    assert body["plan_id"].startswith("plan_")
    assert "frontend_view" in body
    assert "raw" in body
    # FE shape: flat steps[]
    assert isinstance(body["frontend_view"]["steps"], list)
    assert body["frontend_view"]["total_steps"] == 1
    # Raw shape: rich procedures[]
    assert body["raw"]["experiment_type"] == "mock-experiment"
    assert isinstance(body["raw"]["procedures"], list)


def test_protocol_with_plan_id_chains_off_lit_review(protocol_client):
    """Form A (chain): /lit-review returns plan_id; /protocol picks it up."""
    lr = protocol_client.post("/lit-review", json=_valid_body()).get_json()
    plan_id = lr["plan_id"]

    r = protocol_client.post("/protocol", json={"plan_id": plan_id})
    assert r.status_code == 200
    body = r.get_json()
    assert body["plan_id"] == plan_id  # SAME plan reused


def test_protocol_with_unknown_plan_id_returns_400(protocol_client):
    r = protocol_client.post("/protocol", json={"plan_id": "plan_does_not_exist"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_request"


def test_protocol_missing_body_returns_400(protocol_client):
    r = protocol_client.post("/protocol", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_request"


def test_protocol_pipeline_error_does_not_leak_internals(protocol_client, monkeypatch):
    def _broken(hypothesis, **kwargs):
        raise RuntimeError("upstream OOM at /var/lib/secret-path")
    monkeypatch.setattr(flask_app.protocol_stage, "run_protocol_only", _broken)
    r = protocol_client.post("/protocol", json=_valid_body())
    assert r.status_code == 500
    body = r.get_json()
    assert body["error"] == "pipeline_error"
    assert "secret-path" not in body["detail"]
    assert "OOM" not in body["detail"]


# =============================================================================
# /materials — Stage 3 endpoint
# =============================================================================

def test_materials_with_plan_id_having_protocol_returns_200(protocol_client):
    """Happy path: /lit-review → /protocol → /materials."""
    lr = protocol_client.post("/lit-review", json=_valid_body()).get_json()
    plan_id = lr["plan_id"]
    protocol_client.post("/protocol", json={"plan_id": plan_id})

    r = protocol_client.post("/materials", json={"plan_id": plan_id})
    assert r.status_code == 200
    body = r.get_json()
    assert body["plan_id"] == plan_id
    # FE shape: grouped
    assert isinstance(body["frontend_view"]["groups"], list)
    assert body["frontend_view"]["groups"][0]["items"][0]["name"] == "DMEM"
    # Raw shape: flat
    assert body["raw"]["materials"][0]["name"] == "DMEM"


def test_materials_with_plan_id_without_protocol_returns_400(protocol_client):
    """If a plan exists but has no protocol stage output yet, /materials
    must NOT silently re-run protocol — it surfaces the missing dependency
    so the FE knows to call /protocol first."""
    lr = protocol_client.post("/lit-review", json=_valid_body()).get_json()
    plan_id = lr["plan_id"]
    # NOTE: no /protocol call before /materials.
    r = protocol_client.post("/materials", json={"plan_id": plan_id})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "protocol_not_run"
    assert "POST /protocol first" in body["detail"]


def test_materials_with_structured_form_runs_protocol_implicitly(protocol_client):
    """Form B convenience: passing a structured hypothesis to /materials
    creates a fresh plan, runs protocol implicitly, then runs materials.
    Slow in production, but lets a curl test exercise the whole pipeline
    in one call."""
    r = protocol_client.post("/materials", json=_valid_body())
    assert r.status_code == 200
    body = r.get_json()
    assert body["plan_id"].startswith("plan_")
    assert body["frontend_view"]["total_unique_items"] == 1


def test_materials_pipeline_error_does_not_leak_internals(protocol_client, monkeypatch):
    """Same security guarantee as the other endpoints: 500 with sanitized
    detail, no internal exception text in the response."""
    lr = protocol_client.post("/lit-review", json=_valid_body()).get_json()
    plan_id = lr["plan_id"]
    protocol_client.post("/protocol", json={"plan_id": plan_id})

    def _broken(protocol):
        raise RuntimeError("rollup blew up at /etc/leak")
    monkeypatch.setattr(flask_app.protocol_stage, "run_materials_only", _broken)
    r = protocol_client.post("/materials", json={"plan_id": plan_id})
    assert r.status_code == 500
    body = r.get_json()
    assert body["error"] == "pipeline_error"
    assert "/etc/leak" not in body["detail"]
