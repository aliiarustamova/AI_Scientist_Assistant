"""Create / load / save the ExperimentPlan blackboard as JSON on disk."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from src.types import (
    ALL_STAGES,
    ExperimentPlan,
    ExperimentPlanMeta,
    Hypothesis,
    StageName,
    StageStatusNotStarted,
    now,
)

PLANS_DIR = Path("plans")


def _initial_status() -> dict[StageName, StageStatusNotStarted]:
    return {stage: StageStatusNotStarted() for stage in ALL_STAGES}


def create_plan(hypothesis: Hypothesis, model_id: str) -> ExperimentPlan:
    return ExperimentPlan(
        id=f"plan_{uuid.uuid4().hex[:12]}",
        hypothesis=hypothesis,
        status=_initial_status(),
        created_at=now(),
        updated_at=now(),
        meta=ExperimentPlanMeta(generated_at=now(), model_id=model_id),
    )


def plan_path(plan_id: str) -> Path:
    return PLANS_DIR / f"{plan_id}.json"


def save_plan(plan: ExperimentPlan) -> Path:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    path = plan_path(plan.id)
    plan.updated_at = now()
    # encoding=utf-8 is required: Windows defaults to cp1252 which chokes on
    # science Unicode (e.g. minus sign U+2212, mu U+03BC, degree signs).
    path.write_text(plan.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    return path


def load_plan(plan_id: str) -> ExperimentPlan:
    return ExperimentPlan.model_validate_json(plan_path(plan_id).read_text(encoding="utf-8"))
