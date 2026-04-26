"""Stage 6: Validation.

Mix of deterministic and LLM:

  Deterministic (no LLM):
    - aggregate procedure.success_criteria into experiment-level criteria
    - aggregate outline.overall_controls + per-procedure controls
    - extract effect size from hypothesis.expected via regex
    - compute n_per_group via the standard two-sample formula
    - assemble expected_outcome_summary + go_no_go_threshold from the
      hypothesis fields

  LLM (one call):
    - failure_modes — REQUIRED to cite a specific procedure or step.
      Output schema enforces it; the parser drops anything without a
      grounded citation.

Defensibility / reproducibility:
  - Every SuccessCriterion + Control + FailureMode carries `derived_from`
    or `cites` — researchers can audit by jumping to the source.
  - Power calculation surfaces the formula and every assumption used
    (alpha, power, std-deviation guess, distribution).
  - Effect size extraction is regex-based (deterministic). Same
    hypothesis.expected -> same EffectSize.
  - Module-level `methodology` string is included in the output so the
    audit trail lives WITH the data.
"""

from __future__ import annotations

import json
import math
import re
from typing import Optional

from src.clients import llm
from src.types import (
    Control,
    EffectSize,
    FailureMode,
    Hypothesis,
    PowerCalculation,
    Procedure,
    ProtocolGenerationOutput,
    SuccessCriterion,
    ValidationOutput,
)


# --------------------------------------------------------------------------
# Effect size extraction — regex on hypothesis.expected
# --------------------------------------------------------------------------
# Patterns we recognize, in priority order. Rules:
#   - "+15 percentage points" -> percent_change_absolute, value=15
#   - "by at least 30%" or "30% improvement" -> percent_change_relative
#   - "10x" / "10-fold" / "ten-fold" -> fold_change
#   - "Cohen's d >= 0.5" / "d=0.5" -> cohens_d
# Each match yields an EffectSize plus a `derived_from` citation that
# embeds the matched substring so researchers can audit.

_EFFECT_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # percent_change_absolute: "+15 percentage points", "15 percentage points",
    # "by at least 15 percentage points"
    ("percent_change_absolute",
     "absolute percentage-point difference",
     re.compile(r"(?:[+]|by\s+at\s+least\s+|of\s+at\s+least\s+|of\s+)?(\d+(?:\.\d+)?)\s*percentage\s*points?", re.IGNORECASE)),
    # cohens_d: "Cohen's d >= 0.5", "d=0.5", "d of 0.5"
    ("cohens_d",
     "Cohen's d",
     re.compile(r"(?:cohen'?s\s+d|effect\s+size\s+d|^d)\s*[>=:]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)),
    # fold_change: "10-fold", "10x", "ten-fold"
    ("fold_change",
     "fold change",
     re.compile(r"(\d+(?:\.\d+)?)\s*[-]?(?:fold|x\b|×)", re.IGNORECASE)),
    # percent_change_relative: "by at least 30%", "30% improvement"
    ("percent_change_relative",
     "relative percent change",
     re.compile(r"(?:by\s+at\s+least\s+|of\s+at\s+least\s+|by\s+|of\s+)(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)),
]


def extract_effect_size(expected_text: str) -> Optional[EffectSize]:
    """Regex-extract an EffectSize from hypothesis.expected. Returns
    None when no quantitative pattern matches; the caller falls back
    to an 'unspecified' effect size with assumptions documented."""
    text = (expected_text or "").strip()
    if not text:
        return None
    for type_name, _label, pattern in _EFFECT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                value = float(m.group(1))
            except (ValueError, IndexError):
                continue
            # The matched substring (with up to 30 chars of context on
            # either side) goes into derived_from so the audit trail is
            # readable.
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            snippet = text[start:end].strip().replace("\n", " ")
            return EffectSize(
                value=value,
                type=type_name,
                derived_from=f"hypothesis.expected: '...{snippet}...'",
            )
    return None


# --------------------------------------------------------------------------
# Power calculation — standard two-sample formula
# --------------------------------------------------------------------------

# z-scores for common alpha (two-tailed) and power (one-tailed beta)
_Z_ALPHA_TWO_TAILED = {0.05: 1.96, 0.01: 2.576, 0.10: 1.645}
_Z_BETA = {0.80: 0.842, 0.90: 1.282, 0.95: 1.645, 0.70: 0.524}


def _cohens_d_from_effect(effect: EffectSize, fallback_cv: float = 0.20) -> tuple[float, list[str]]:
    """Convert any effect-size type to a Cohen's d for the standard
    two-sample n formula. Returns (d, assumptions[]).

    Conversions are explicit and conservative:
      - percent_change_absolute (e.g. 15 percentage points): assume
        CV (coefficient of variation) ~20% on each arm; d ≈
        15/(20*sqrt(2)) ≈ 0.53 in this example. Surface the CV
        assumption in the returned list.
      - percent_change_relative: same CV assumption applied to the
        difference of means.
      - fold_change: log-transform; treat 2-fold as Cohen's d ~0.7
        (Glass's delta on log scale with CV~0.2). Conservative.
      - cohens_d: pass through.

    All assumptions are returned so the FE can show them in the
    methodology / audit panel."""
    assumptions: list[str] = []
    if effect.type == "cohens_d":
        return (effect.value, assumptions)
    cv = fallback_cv
    assumptions.append(
        f"Coefficient of variation (CV) on outcome assumed = {cv:.0%} "
        f"on each arm — a typical biological-measurement default. "
        f"Cohen's d = absolute_diff / (CV * sqrt(2))."
    )
    if effect.type == "percent_change_absolute":
        # delta is in absolute percentage points (e.g., 15). Treat the
        # outcome scale as a percentage; SD ≈ CV * mean. Without knowing
        # the mean we assume mean=50% (mid-range) → SD = 50*CV. Cohen's d
        # = delta / SD.
        sd = 50.0 * cv
        d = effect.value / (sd * math.sqrt(2))
        assumptions.append(
            "Outcome mean assumed to be ~50% on the absolute scale "
            "(mid-range) — used to convert percentage-point delta to "
            "Cohen's d. Adjust if your outcome lives in a tail."
        )
        return (d, assumptions)
    if effect.type == "percent_change_relative":
        # relative % change of mean. SD = CV * mean; cancel mean: d ≈
        # rel/100 / (CV * sqrt(2)).
        d = (effect.value / 100.0) / (cv * math.sqrt(2))
        return (d, assumptions)
    if effect.type == "fold_change":
        # log-fold-change; SD assumed cv on log scale. Cohen's d on log
        # scale: log(fold) / (CV * sqrt(2)).
        try:
            d = math.log(max(effect.value, 1.0001)) / (cv * math.sqrt(2))
        except ValueError:
            d = 0.5
        assumptions.append(
            "Fold-change handled on log scale; assumed log-normal SD."
        )
        return (d, assumptions)
    if effect.type == "odds_ratio":
        # Convert OR to Cohen's d via the Hasselblad-Hedges
        # log(OR)*sqrt(3)/pi approximation.
        try:
            d = math.log(effect.value) * math.sqrt(3) / math.pi
        except ValueError:
            d = 0.5
        return (d, assumptions)
    # Unknown type → conservative medium effect d=0.5
    assumptions.append(
        f"Effect-size type '{effect.type}' not handled; assumed medium "
        f"effect (Cohen's d = 0.5)."
    )
    return (0.5, assumptions)


def compute_power_calculation(
    effect: EffectSize,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    groups: int = 2,
) -> PowerCalculation:
    """Compute n_per_group via the standard two-sample formula:
        n = 2 * ((z_alpha/2 + z_beta) / d)^2  (per arm)
    Surfaces formula + every assumption used."""
    z_alpha = _Z_ALPHA_TWO_TAILED.get(alpha, 1.96)
    z_beta = _Z_BETA.get(power, 0.842)
    d, eff_assumptions = _cohens_d_from_effect(effect)
    if d <= 0:
        d = 0.5  # ultra-defensive

    n_per_group = math.ceil(2 * ((z_alpha + z_beta) / d) ** 2)

    formula = (
        f"Two-sample t-test sample size: n_per_arm = "
        f"2 * ((z_α/2 + z_β) / d)^2 with z_α/2 = {z_alpha} (two-tailed "
        f"α = {alpha}), z_β = {z_beta} (power = {power}), d = {d:.2f} "
        f"(Cohen's d derived from effect size)."
    )

    assumptions = [
        "Two-sample t-test power formula (standard textbook).",
        f"Two-tailed α = {alpha}, target power = {power}.",
        "Sample sizes equal across arms; independent observations.",
        "Approximately normal outcome distribution (Welch's t-test "
        "is robust for moderate departures).",
        *eff_assumptions,
    ]

    rationale = (
        f"Effect size '{effect.type}' was derived from "
        f"{effect.derived_from!r}. The Cohen's d-equivalent of "
        f"{d:.2f} was plugged into the standard two-sample n formula. "
        f"Adjust if your outcome scale or expected variance differs."
    )

    return PowerCalculation(
        statistical_test="two-sample t-test (two-tailed)",
        alpha=alpha,
        power=power,
        effect_size=effect,
        n_per_group=n_per_group,
        groups=groups,
        total_n=n_per_group * groups,
        formula=formula,
        assumptions=assumptions,
        rationale=rationale,
    )


# --------------------------------------------------------------------------
# Aggregations from procedures (deterministic)
# --------------------------------------------------------------------------

def aggregate_success_criteria(
    protocol: ProtocolGenerationOutput,
    hypothesis: Hypothesis,
) -> list[SuccessCriterion]:
    """Build the experiment-level success_criteria list from procedure-
    level criteria + an experiment-wide criterion derived from the
    hypothesis's dependent variable. Each carries `derived_from` for
    audit."""
    out: list[SuccessCriterion] = []
    next_id = 1

    # Experiment-wide criterion from hypothesis.dependent + expected
    if hypothesis.structured.dependent.strip():
        threshold = hypothesis.structured.expected.strip() or "see hypothesis"
        out.append(SuccessCriterion(
            id=f"sc{next_id}",
            criterion=f"Primary outcome: {hypothesis.structured.dependent.strip()}",
            measurement_method="see procedure-level criteria",
            threshold=threshold,
            derived_from="hypothesis.dependent + hypothesis.expected",
        ))
        next_id += 1

    # Per-procedure criteria
    for proc in protocol.procedures:
        for c in proc.success_criteria:
            out.append(SuccessCriterion(
                id=f"sc{next_id}",
                criterion=c.what,
                measurement_method=c.how_measured,
                threshold=c.threshold or "(not specified)",
                derived_from=f"procedure '{proc.name}'",
            ))
            next_id += 1

    return out


def aggregate_controls(protocol: ProtocolGenerationOutput) -> list[Control]:
    """Aggregate controls from outline.overall_controls + per-procedure
    controls. Best-effort classify type from text (positive/negative/
    vehicle/sham); defaults to 'negative' when ambiguous so the FE shows
    something rather than nothing."""
    out: list[Control] = []
    seen: set[str] = set()  # case-insensitive dedup

    def _classify(text: str) -> str:
        t = text.lower()
        if "positive control" in t or "positive ctrl" in t:
            return "positive"
        if "negative control" in t or "negative ctrl" in t or "blank" in t:
            return "negative"
        if "vehicle" in t or "carrier" in t or "diluent" in t:
            return "vehicle"
        if "sham" in t or "mock" in t:
            return "sham"
        return "negative"  # default

    for proc in protocol.procedures:
        for ctl in proc.controls if hasattr(proc, "controls") else []:
            key = ctl.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(Control(
                name=ctl.strip(),
                type=_classify(ctl),
                purpose="see procedure context",
                derived_from=f"procedure '{proc.name}'.controls",
            ))

    # Step-level controls (each step.controls is a list[str])
    for proc in protocol.procedures:
        for step in proc.steps:
            for ctl in step.controls or []:
                key = ctl.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(Control(
                    name=ctl.strip(),
                    type=_classify(ctl),
                    purpose="see step context",
                    derived_from=f"step {step.n} of procedure '{proc.name}'",
                ))

    return out


# --------------------------------------------------------------------------
# Failure modes (LLM, citations required)
# --------------------------------------------------------------------------

FAILURE_MODES_SYSTEM = """You audit experimental designs for ways they can fail to give a clean answer to the hypothesis.

You receive the hypothesis and the per-procedure summary of an experiment plan. Identify failure modes — points where the protocol could go wrong such that the dependent variable becomes unreadable, biased, or confounded.

EVERY failure mode you emit MUST cite a specific procedure (by name) or step (by number + procedure). Failure modes without a procedure/step citation will be DROPPED by the parser. The citation is what makes the concern auditable — researchers will jump to that procedure to evaluate.

Quality bar:
- 3-6 failure modes total (not exhaustive — pick the most likely / highest-impact)
- Each must be SPECIFIC to this experiment, not generic ("contamination" with no procedure cite is too vague)
- Mitigation should be actionable: a step the researcher can add or check

Return ONLY a single valid JSON object:
{
  "failure_modes": [
    {
      "mode": "string (one-line description of what fails)",
      "likely_cause": "string (why this failure happens)",
      "mitigation": "string (what to add/check to prevent or detect it)",
      "cites": "string (procedure name OR 'step N (procedure X)')"
    }
  ]
}"""

FAILURE_MODES_USER_TMPL = """Hypothesis (structured):
- Subject: {subject}
- Intervention: {independent}
- Measurement: {dependent}
- Conditions: {conditions}
- Expected outcome: {expected}
- Research question: {research_question}

Procedures ({n}):
{procedures_blob}"""


def _format_procedure_for_failures(p: Procedure) -> str:
    head_steps = []
    for s in p.steps[:6]:
        body = s.body_md.strip().replace("\n", " ")
        if len(body) > 200:
            body = body[:200] + "…"
        head_steps.append(f"  - step {s.n}: {body}")
    return (
        f"procedure: {p.name}\n"
        f"  intent: {p.intent}\n"
        f"  steps:\n" + ("\n".join(head_steps) if head_steps else "  (no steps)")
    )


def generate_failure_modes(
    hypothesis: Hypothesis,
    protocol: ProtocolGenerationOutput,
) -> list[FailureMode]:
    """One LLM call. Output schema enforces a citation per failure mode;
    the parser drops anything missing or hallucinated."""
    s = hypothesis.structured
    procs_blob = "\n\n".join(_format_procedure_for_failures(p) for p in protocol.procedures) \
        or "(no procedures)"
    user = FAILURE_MODES_USER_TMPL.format(
        subject=s.subject,
        independent=s.independent,
        dependent=s.dependent,
        conditions=s.conditions,
        expected=s.expected,
        research_question=s.research_question,
        n=len(protocol.procedures),
        procedures_blob=procs_blob,
    )

    try:
        parsed = llm.complete_json(FAILURE_MODES_SYSTEM, user, agent_name="Failure modes")
    except Exception:
        return []

    raw = parsed.get("failure_modes") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []

    # Defense: build the set of procedure-name and step-citation strings
    # the LLM is allowed to cite. Anything that doesn't match gets dropped.
    proc_names = {p.name for p in protocol.procedures}

    out: list[FailureMode] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        mode = str(entry.get("mode") or "").strip()
        cause = str(entry.get("likely_cause") or "").strip()
        mitigation = str(entry.get("mitigation") or "").strip()
        cites = str(entry.get("cites") or "").strip()
        if not (mode and cause and mitigation and cites):
            continue
        # Citation must mention a known procedure name. We're permissive:
        # any procedure name appearing as a substring of `cites` counts,
        # since the LLM may write "step 3 (procedure 'Cell Freezing')".
        if not any(pn in cites for pn in proc_names):
            continue
        out.append(FailureMode(
            mode=mode,
            likely_cause=cause,
            mitigation=mitigation,
            cites=cites,
        ))
    return out


# --------------------------------------------------------------------------
# Top-level orchestrator
# --------------------------------------------------------------------------

def compute_validation(
    hypothesis: Hypothesis,
    protocol: ProtocolGenerationOutput,
) -> ValidationOutput:
    """Run Stage 6 over an existing protocol. Mostly deterministic
    (regex effect-size + formula-based n + aggregation); one LLM call
    for failure modes."""
    success_criteria = aggregate_success_criteria(protocol, hypothesis)
    controls = aggregate_controls(protocol)
    failure_modes = generate_failure_modes(hypothesis, protocol)

    # Power calculation (deterministic — None when no effect size could
    # be extracted, since a fabricated number is worse than no number).
    effect = extract_effect_size(hypothesis.structured.expected)
    if effect is None:
        power_calc = None
        power_methodology_note = (
            "No quantitative effect size found in hypothesis.expected; "
            "power calculation skipped. Add a target effect (e.g. "
            "'+15 percentage points', 'Cohen's d ≥ 0.5', '2-fold change') "
            "to the hypothesis to enable a sample-size estimate."
        )
    else:
        power_calc = compute_power_calculation(effect)
        power_methodology_note = (
            f"Effect size extracted from hypothesis.expected; n = "
            f"{power_calc.n_per_group}/group via standard formula."
        )

    expected_outcome_summary = (
        hypothesis.structured.expected.strip()
        or f"{hypothesis.structured.dependent.strip()} differs between arms."
    )
    go_no_go_threshold = (
        hypothesis.structured.expected.strip()
        or "Primary outcome shows the predicted direction with p < 0.05."
    )

    methodology = (
        "Validation block assembled deterministically from the protocol "
        f"and hypothesis: {len(success_criteria)} success criteria "
        f"aggregated (each cites its source procedure or hypothesis "
        f"field), {len(controls)} controls, {len(failure_modes)} "
        f"failure modes (LLM-derived; each citation validated against "
        f"the procedure list, ungrounded entries dropped). "
        f"{power_methodology_note}"
    )

    return ValidationOutput(
        success_criteria=success_criteria,
        controls=controls,
        failure_modes=failure_modes,
        power_calculation=power_calc,
        expected_outcome_summary=expected_outcome_summary,
        go_no_go_threshold=go_no_go_threshold,
        methodology=methodology,
    )
