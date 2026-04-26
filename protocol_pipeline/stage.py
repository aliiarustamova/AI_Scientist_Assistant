"""Stage 2 + 3 orchestrator.

Reads:  hypothesis (and protocols.io static samples by name)
Writes: ExperimentPlan.protocol (ProtocolGenerationOutput)
        ExperimentPlan.materials (MaterialsOutput)

Flow:
  1. Load + normalize protocols.io samples (offline; from
     pipeline_output_samples/protocols_io/).
  2. Relevance filter (1 LLM call): drop sources below threshold.
  3. Architect (1 LLM call): emit ProtocolOutline with 3-8 procedures.
  4. Procedure writers (N parallel LLM calls): one per procedure.
  5. Materials roll-up (1 LLM call): consolidate equipment + reagents
     across procedures with concrete specs.
  6. Validate + bind to Pydantic types, write to blackboard.

Total LLM calls: 3 + N_procedures (typically 7-9 per run).

This stage runs against the static samples committed to the repo, NOT
against a live protocols.io fetch. The teammate's protocols.io client
is being built on a separate branch; the only swap needed when it
lands is the source loader (currently `sources.load_all_samples()`).
"""

from __future__ import annotations

import re
from typing import Optional

from src.types import (
    CitedProtocol,
    ExperimentPlan,
    Hypothesis,
    MaterialsOutput,
    Procedure,
    ProtocolGenerationOutput,
    ProtocolStep,
    StageStatusComplete,
    StageStatusFailed,
    StageStatusRunning,
    now,
)

from .architect import ProtocolOutline, plan_outline
from .materials import roll_up_materials
from .relevance import filter_relevant
from .sources import (
    NormalizedProtocol,
    fetch_live_candidates,
    load_all_samples,
)
from .writer import write_procedures_parallel


# --------------------------------------------------------------------------
# ISO 8601 duration sum — for procedure / protocol total_duration
# --------------------------------------------------------------------------
# We compute totals deterministically rather than asking the writer to
# emit them. Researchers plan their day around these numbers; even an
# off-by-an-hour LLM estimate is worse than no estimate at all.

_ISO_DURATION_RE = re.compile(
    r"^P"
    r"(?:(\d+)Y)?"        # years
    r"(?:(\d+)M)?"        # months (M *before* T)
    r"(?:(\d+)W)?"        # weeks
    r"(?:(\d+)D)?"        # days
    r"(?:T"
    r"(?:(\d+)H)?"        # hours
    r"(?:(\d+)M)?"        # minutes (M *after* T)
    r"(?:(\d+(?:\.\d+)?)S)?"   # seconds (allow fractional)
    r")?$"
)


def _iso_duration_to_seconds(iso: str) -> Optional[float]:
    """Parse an ISO 8601 duration string to total seconds. Returns None for
    malformed / empty input. Years -> 365 days, months -> 30 days (rough,
    but Y/M-without-T are vanishingly rare in lab protocols)."""
    s = (iso or "").strip()
    if not s or s == "P":
        return None
    m = _ISO_DURATION_RE.match(s)
    if not m:
        return None
    years, months, weeks, days, hours, minutes, seconds = m.groups()
    if not any((years, months, weeks, days, hours, minutes, seconds)):
        return None  # "P" or "PT" alone is malformed
    total = 0.0
    if years: total += int(years) * 365 * 86400
    if months: total += int(months) * 30 * 86400
    if weeks: total += int(weeks) * 7 * 86400
    if days: total += int(days) * 86400
    if hours: total += int(hours) * 3600
    if minutes: total += int(minutes) * 60
    if seconds: total += float(seconds)
    return total


def _seconds_to_iso_duration(total: float) -> str:
    """Format total seconds back to a readable ISO 8601 duration. We use
    days/hours/minutes — never weeks or months (FE-side `_humanize_duration`
    can format days into 'X d' / hours into 'X h' as needed)."""
    if total <= 0:
        return "PT0S"
    secs_int = int(total)
    days, rem = divmod(secs_int, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    date_part = f"{days}D" if days else ""
    time_parts = ""
    if hours: time_parts += f"{hours}H"
    if minutes: time_parts += f"{minutes}M"
    if secs and not (days or hours or minutes):
        time_parts += f"{secs}S"

    if not date_part and not time_parts:
        return "PT0S"
    out = "P" + date_part
    if time_parts:
        out += "T" + time_parts
    return out


_QUERY_SYSTEM = """You extract candidate search queries for protocols.io from a structured scientific hypothesis.

protocols.io's full-text search ANDs every token, so multi-word queries with rare combinations collapse to zero hits. We try your queries in order until one returns results, so RANK FROM MOST-LIKELY-TO-MATCH-MANY-PROTOCOLS to MOST-SPECIFIC.

Strategy:
- First query: ONE common single-concept word — the broad technique or reagent class (e.g., "trehalose", "ELISA", "Lactobacillus", "FITC-dextran").
- Second query: a different angle in case the first misses (e.g., the assay name, organism class, or alternative reagent).
- Third query (optional): one more concept word.

Each query is 1-3 words MAX. Single words preferred. Avoid acronyms specific to one paper (e.g., not "LGG", use "Lactobacillus").

Examples:
- HeLa cryopreservation with trehalose vs DMSO →
    trehalose
    cryopreservation
    cell freezing
- CRP detection by paper biosensor →
    C-reactive protein
    ELISA
    biosensor
- Gut barrier in mice with Lactobacillus rhamnosus GG, FITC-dextran assay →
    Lactobacillus
    FITC-dextran
    intestinal permeability

Output ONLY the queries, ONE PER LINE, no numbering, no explanation, no quotes."""


def _query_for_hypothesis(hypothesis: Hypothesis) -> list[str]:
    """LLM-driven query extraction for protocols.io. Returns a list of 1-3
    candidate queries, ranked from most-likely-to-match (broad concept)
    to most-specific. Caller tries them in order.

    Falls back to the structured.subject field's first word if the LLM
    call fails — better than nothing."""
    s = hypothesis.structured
    user = (
        f"Subject: {s.subject}\n"
        f"Intervention: {s.independent}\n"
        f"Measurement: {s.dependent}\n"
        f"Conditions: {s.conditions}\n"
        f"Expected: {s.expected}"
    )
    try:
        from src.clients import llm
        raw = llm.complete(_QUERY_SYSTEM, user).strip()
        # Parse one-per-line, drop blanks, strip wrapping quotes / bullets.
        lines: list[str] = []
        for line in raw.splitlines():
            t = line.strip().strip('"').strip("'").lstrip("•-*0123456789.) ").strip()
            if t and 1 <= len(t) <= 60:
                lines.append(t)
        if lines:
            return lines[:3]
    except Exception:
        pass
    # Fallback: subject's first word (single concept).
    subject = (s.subject or "").strip()
    if subject:
        return [subject.split()[0]]
    return ["protocol"]


def _sum_iso8601_durations(durations: list[Optional[str]]) -> Optional[str]:
    """Sum a list of ISO 8601 duration strings. Returns None if ANY input
    is missing or malformed — a partial sum would mislead a researcher
    budgeting their day. Conservative-by-design: better no estimate than
    a wrong one."""
    if not durations:
        return None
    total = 0.0
    for d in durations:
        if not d:
            return None
        s = _iso_duration_to_seconds(d)
        if s is None:
            return None
        total += s
    return _seconds_to_iso_duration(total)


# --------------------------------------------------------------------------
# Output bundle (what the runner returns to its caller)
# --------------------------------------------------------------------------

class StageResult:
    """Convenience bag for the runner's output. Two Pydantic blocks plus
    the intermediate outline (handy for debugging / sample dumps)."""
    def __init__(
        self,
        protocol: ProtocolGenerationOutput,
        materials: MaterialsOutput,
        outline: ProtocolOutline,
    ) -> None:
        self.protocol = protocol
        self.materials = materials
        self.outline = outline


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def run_protocol_only(
    hypothesis: Hypothesis,
    *,
    sources: list[NormalizedProtocol] | None = None,
    relevance_threshold: float = 0.2,
    max_writer_workers: int = 5,
) -> tuple[ProtocolGenerationOutput, ProtocolOutline]:
    """Run Stage 2 only: relevance + architect + writers. Skips materials
    roll-up so the FE can render the protocol as soon as it's ready and
    fetch materials in a separate request. Returns the protocol output
    and the intermediate outline (kept for debugging / sample dumps).

    Source resolution (when `sources` is not explicitly provided):
      1. Try a live protocols.io fetch using the hypothesis subject as
         the search query (single-concept queries score better against
         protocols.io's AND'd-token search than verbose hypotheses).
      2. If that returns nothing — token missing, network down, no
         matches, etc. — fall back to the static samples committed to
         the repo. Tests + offline dev keep working.
      3. If both empty, the architect / writer agents synthesize from
         common knowledge alone (still produces a usable protocol;
         the confidence banner reflects the lack of grounding).
    """
    if sources is None:
        # Try ranked candidate queries until one returns hits. The query
        # extractor outputs broad-to-specific so we don't lock onto a
        # zero-result over-narrow query (e.g. "Lactobacillus rhamnosus GG"
        # -> 0 hits) when a broader fallback ("Lactobacillus" -> 8 hits)
        # is one retry away.
        sources = []
        queries = _query_for_hypothesis(hypothesis)
        for query in queries:
            live = fetch_live_candidates(query, limit=5)
            if live:
                sources = list(live.values())
                break
        if not sources:
            # All candidate queries returned 0 — fall back to static
            # samples. Tests + offline dev keep working; the confidence
            # banner will reflect the lack of grounding.
            sources = list(load_all_samples().values())

    # 1. Relevance filter
    scored = filter_relevant(hypothesis, sources, keep_threshold=relevance_threshold)

    # 2. Architect
    outline = plan_outline(hypothesis, scored)

    # 3. Procedure writers (parallel)
    sources_by_id = {p.id: p for p in sources}
    procedures = write_procedures_parallel(
        hypothesis, outline.procedures, sources_by_id,
        max_workers=max_writer_workers,
    )

    # 4. Compute total_duration per procedure (deterministic sum of step
    # durations). Mutates Procedures in place. None when ANY step is
    # missing a duration — a partial sum would be misleading.
    for proc in procedures:
        proc.total_duration = _sum_iso8601_durations([s.duration for s in proc.steps])

    # 5. Build flat steps view (re-numbered across procedures for FE checklist)
    flat_steps: list[ProtocolStep] = []
    counter = 1
    for proc in procedures:
        for s in proc.steps:
            flat_steps.append(s.model_copy(update={"n": counter}))
            counter += 1

    # 5. Cited protocols: every source the architect routed to at least one procedure
    referenced_ids: set[str] = set()
    for proc in procedures:
        referenced_ids.update(proc.source_protocol_ids)

    cited: list[CitedProtocol] = []
    for sp in scored:
        if sp.protocol.id not in referenced_ids:
            continue
        cited.append(CitedProtocol(
            doi=sp.protocol.doi,
            protocols_io_id=sp.protocol.id,
            title=sp.protocol.title,
            contribution_weight=round(sp.score.score, 2),
        ))

    # Experiment-wide total: sum the per-procedure totals (which we just
    # computed above). Same conservative behavior — None if any procedure
    # came back None.
    total_duration = _sum_iso8601_durations([p.total_duration for p in procedures])

    protocol = ProtocolGenerationOutput(
        experiment_type=outline.experiment_type,
        domain=outline.domain or hypothesis.domain,
        procedures=procedures,
        steps=flat_steps,
        cited_protocols=cited,
        regulatory_requirements=[],   # Stage 6 (Validation) populates this
        assumptions=outline.overall_assumptions,
        total_steps=len(flat_steps),
        source_protocol_ids=sorted(referenced_ids),
        total_duration=total_duration,
    )
    return protocol, outline


def run_materials_only(protocol: ProtocolGenerationOutput) -> MaterialsOutput:
    """Run Stage 3 only: materials roll-up over an existing
    ProtocolGenerationOutput. Cheap (one LLM call) — designed for a
    /materials endpoint that chains off a previously-saved protocol
    rather than re-running Stage 2."""
    return roll_up_materials(protocol.procedures)


def run_timeline_only(protocol: ProtocolGenerationOutput):
    """Run Stage 5 only: deterministic timeline computation. Designed
    for a /timeline endpoint that chains off a previously-saved
    protocol. No LLM call — pure summation of step durations."""
    # Lazy import to avoid a circular dep with timeline.py (which
    # imports `_sum_iso8601_durations` from this module).
    from .timeline import compute_timeline
    return compute_timeline(protocol)


def run_validation_only(
    hypothesis: Hypothesis,
    protocol: ProtocolGenerationOutput,
):
    """Run Stage 6 only: validation block (success criteria, controls,
    failure modes, power calc). Mostly deterministic; one LLM call for
    failure modes. Designed for a /validation endpoint that chains off
    a previously-saved protocol."""
    from .validation import compute_validation
    return compute_validation(hypothesis, protocol)


def run_critique_only(
    hypothesis: Hypothesis,
    protocol: ProtocolGenerationOutput,
):
    """Run Stage 7 only: design critique. One LLM call with citation
    enforcement (ungrounded risks/confounders dropped by the parser).
    Designed for a /critique endpoint that chains off a previously-
    saved protocol."""
    from .critique import compute_critique
    return compute_critique(hypothesis, protocol)


def run(
    hypothesis: Hypothesis,
    *,
    sources: list[NormalizedProtocol] | None = None,
    relevance_threshold: float = 0.2,
    max_writer_workers: int = 5,
) -> StageResult:
    """Run the full Stage 2 + 3 pipeline (protocol + materials together).
    Use `run_protocol_only` / `run_materials_only` for the split flow."""
    protocol, outline = run_protocol_only(
        hypothesis,
        sources=sources,
        relevance_threshold=relevance_threshold,
        max_writer_workers=max_writer_workers,
    )
    materials = run_materials_only(protocol)
    return StageResult(protocol=protocol, materials=materials, outline=outline)


def run_and_write(
    plan: ExperimentPlan,
    *,
    sources: list[NormalizedProtocol] | None = None,
    relevance_threshold: float = 0.2,
    max_writer_workers: int = 5,
) -> ExperimentPlan:
    """Run the pipeline and write the results to the shared blackboard.

    Updates `plan.protocol`, `plan.materials`, BOTH stage statuses, and
    `plan.updated_at`. The blackboard pattern requires every consumer to
    be able to ask "did Stage 2 complete?" and "when was the plan last
    touched?" — silently mutating the output fields without updating
    status leaves downstream stages unable to gate on completion.

    Both stages share the same lifecycle here because they emit together
    in this pipeline (the materials roll-up runs as the final agent of
    the same orchestration). On exception, both are marked failed.
    """
    started = now()
    plan.status["protocol"] = StageStatusRunning(started_at=started)
    plan.status["materials"] = StageStatusRunning(started_at=started)
    plan.updated_at = started

    try:
        result = run(
            plan.hypothesis,
            sources=sources,
            relevance_threshold=relevance_threshold,
            max_writer_workers=max_writer_workers,
        )
    except Exception as exc:
        failed_at = now()
        plan.status["protocol"] = StageStatusFailed(failed_at=failed_at, error=str(exc))
        plan.status["materials"] = StageStatusFailed(failed_at=failed_at, error=str(exc))
        plan.updated_at = failed_at
        raise

    completed_at = now()
    plan.protocol = result.protocol
    plan.materials = result.materials
    plan.status["protocol"] = StageStatusComplete(completed_at=completed_at)
    plan.status["materials"] = StageStatusComplete(completed_at=completed_at)
    plan.updated_at = completed_at
    return plan
