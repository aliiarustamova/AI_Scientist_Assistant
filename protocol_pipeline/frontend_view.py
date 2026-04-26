"""Adapter that projects the rich Stage 2 / Stage 3 outputs onto the
shape the existing React `ExperimentPlan.tsx` page consumes.

The FE was built against a hardcoded mock with a flatter, simpler shape
than the pipeline emits:

  Backend                                       →  Frontend (ExperimentPlan.tsx)
  --------------------------------------------     ----------------------------
  procedures[].steps[] (rich, with params,       →  flat ProtocolStep[] with
    deviations, success_criteria, todos)            {title, detail, citation?,
                                                     phase, meta?}
  materials[] flat with category field           →  MaterialGroup[] grouped by
                                                     {group, description, items}
                                                   each item: {name, purpose,
                                                     supplier, catalog, qty,
                                                     qtyContext?, note?}

This module owns the projection so:
  - API endpoints can return BOTH the rich shape and the FE shape (FE
    upgrades later don't require BE changes).
  - The drift is documented in one place.
  - Tests can pin the mapping down without spinning up the live LLMs.

Two fields the FE renders that we don't have first-class data for yet:
  - phase: derived heuristically from procedure name keywords
    ("preparation"/"setup" → Preparation, "harvest"/"freeze"/"treatment"
    → Experiment, "viability"/"assay"/"measurement"/"ELISA" → Measurement,
    "analysis"/"statistical"/"comparison" → Analysis). Falls back to
    "Experiment" if nothing matches.
  - supplier/catalog: emitted as the literal placeholder string "TBD".
    Stage 4 (Budget) will backfill these with real supplier lookups.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.types import (
    Material,
    MaterialsOutput,
    Procedure,
    ProtocolGenerationOutput,
    ProtocolStep,
    StepParams,
)


# --------------------------------------------------------------------------
# Output shapes (mirror the TypeScript types in
# frontend/src/pages/ExperimentPlan.tsx)
# --------------------------------------------------------------------------

Phase = Literal["Preparation", "Experiment", "Measurement", "Analysis"]


class FEProtocolStep(BaseModel):
    title: str
    detail: str
    citation: Optional[str] = None
    phase: Phase
    meta: Optional[str] = None


class FEReagent(BaseModel):
    name: str
    purpose: str
    supplier: Optional[str] = "TBD"
    catalog: Optional[str] = "TBD"
    qty: str
    qtyContext: Optional[str] = None
    note: Optional[dict] = None  # {kind: "cold"|"lead", text: string}


class FEMaterialGroup(BaseModel):
    group: str
    description: str
    items: list[FEReagent]


class FEProtocolView(BaseModel):
    """What POST /protocol returns to the FE (in addition to the raw rich
    output if we ship both shapes side-by-side)."""
    steps: list[FEProtocolStep]
    experiment_type: str
    total_steps: int
    cited_protocols: list[dict] = Field(default_factory=list)


class FEMaterialsView(BaseModel):
    """What POST /materials returns to the FE."""
    groups: list[FEMaterialGroup]
    total_unique_items: int
    gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Phase classification — heuristic on procedure name + intent
# --------------------------------------------------------------------------

# Stems chosen so we catch inflections (freez → freezing/freezer/freeze;
# thaw → thawing/thawed). Order = priority: Measurement first (most
# specific), then Analysis, then Experiment (concrete actions), and
# Preparation is the catch-all for setup/prep work. We match against the
# procedure NAME ONLY — including the intent caused false positives
# because intents like "...maintain culture..." dragged Experiment work
# into Preparation.
_PHASE_RULES: list[tuple[Phase, tuple[str, ...]]] = [
    ("Measurement", (
        "measurement", "measure", "assay", "elisa", "western", "imaging",
        "viability", "quantif", "spectroph", "fluoresc", "absorbance",
        "detection", "od600", "cell counting", "trypan", "readout",
    )),
    ("Analysis", (
        "data analysis", "statistical", "statistics", "anova", "regression",
        "curve fit", "lod", "performance comparison", "data processing",
    )),
    ("Experiment", (
        "harvest", "treatment", "intervention", "freez", "thaw",
        "incubat", "challenge", "spike", "exposure", "infection",
        "supplement", "gavage", "cryopreserv", "dissection",
        "transfection", "induction", "stimulation",
    )),
    ("Preparation", (
        "preparation", "fabricat", "functionaliz", "calibration",
        "standard curve", "biosensor fabricat", "stock", "media",
        "setup", "set up",
    )),
]


def classify_phase(procedure: Procedure) -> Phase:
    """Map a procedure to the FE's 4-phase enum via keyword heuristic on
    the procedure NAME (not intent — intent prose drags in false
    positives like "in culture" → Preparation). Default: 'Experiment'
    (the bulk of typical lab work). Architect names are descriptive
    enough that the name-only heuristic catches the common cases."""
    name = procedure.name.lower()
    for phase, keywords in _PHASE_RULES:
        if any(kw in name for kw in keywords):
            return phase
    return "Experiment"


# --------------------------------------------------------------------------
# Step adaptation
# --------------------------------------------------------------------------

def _format_meta(params: StepParams) -> Optional[str]:
    """Compact tag the FE shows next to each step. Pick the first
    "interesting" param: temperature > volume > duration > concentration >
    speed. Returns None if no params set."""
    if params.temperature:
        return f"{_fmt_num(params.temperature.value)} {params.temperature.unit}"
    if params.volume:
        return f"{_fmt_num(params.volume.value)} {params.volume.unit}"
    if params.duration:
        return _humanize_duration(params.duration)
    if params.concentration:
        return f"{_fmt_num(params.concentration.value)} {params.concentration.unit}"
    if params.speed:
        return f"{_fmt_num(params.speed.value)} {params.speed.unit}"
    return None


def _fmt_num(v: float) -> str:
    """Drop trailing .0 for whole numbers (e.g. 37.0 → 37, but 1.5 stays)."""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def _humanize_duration(iso: str) -> str:
    """Best-effort ISO-8601 duration → short label (PT5M → 5 min, P1D →
    1 day). Falls back to the raw ISO string if it doesn't match the
    common shapes."""
    s = iso.strip()
    if s.startswith("PT") and s.endswith("M"):
        return f"{s[2:-1]} min"
    if s.startswith("PT") and s.endswith("H"):
        return f"{s[2:-1]} h"
    if s.startswith("PT") and s.endswith("S"):
        return f"{s[2:-1]} s"
    if s.startswith("P") and s.endswith("D") and "T" not in s:
        return f"{s[1:-1]} d"
    if s.startswith("P") and s.endswith("W") and "T" not in s:
        return f"{s[1:-1]} wk"
    return s


def _step_citation(step: ProtocolStep, procedure: Procedure) -> Optional[str]:
    """The FE citation field is a short string. Prefer DOI; fall back to
    the first source_protocol_id of the procedure (a protocols.io ID)."""
    if step.cited_doi:
        return f"doi:{step.cited_doi}"
    if procedure.source_protocol_ids:
        return f"protocols.io/{procedure.source_protocol_ids[0]}"
    return None


def adapt_protocol(protocol: ProtocolGenerationOutput) -> FEProtocolView:
    """Project a ProtocolGenerationOutput into the FE's flat shape."""
    fe_steps: list[FEProtocolStep] = []
    counter = 1
    for proc in protocol.procedures:
        phase = classify_phase(proc)
        for step in proc.steps:
            fe_steps.append(FEProtocolStep(
                title=step.title,
                detail=step.body_md,
                citation=_step_citation(step, proc),
                phase=phase,
                meta=_format_meta(step.params),
            ))
            counter += 1

    cited = [
        {
            "title": cp.title,
            "doi": cp.doi,
            "protocols_io_id": cp.protocols_io_id,
            "contribution_weight": cp.contribution_weight,
        }
        for cp in protocol.cited_protocols
    ]

    return FEProtocolView(
        steps=fe_steps,
        experiment_type=protocol.experiment_type,
        total_steps=len(fe_steps),
        cited_protocols=cited,
    )


# --------------------------------------------------------------------------
# Materials adaptation
# --------------------------------------------------------------------------

# Pydantic Material.category is constrained to these five values.
_GROUP_LABELS: dict[str, tuple[str, str]] = {
    "reagent":    ("Reagents", "Buffers, media, antibodies, and other consumable chemicals."),
    "consumable": ("Consumables", "Disposable plasticware and lab consumables."),
    "equipment":  ("Equipment", "Instruments and durable lab equipment."),
    "cell_line":  ("Cell lines & strains", "Cultured cell lines required for the experiment."),
    "organism":   ("Organisms & samples", "Live animals or biological samples."),
}

_GROUP_ORDER = ["cell_line", "organism", "reagent", "consumable", "equipment"]


def _qty_string(material: Material) -> str:
    """Materials' qty/unit pair → the FE's display string. Empty string
    when neither is set (FE renders that as a dash)."""
    if material.qty is not None and material.unit:
        return f"{_fmt_num(material.qty)} {material.unit}"
    if material.unit:
        return material.unit
    if material.qty is not None:
        return _fmt_num(material.qty)
    return ""


# Storage strings the LLM emits frequently; we map them to the FE's
# cold-chain badge. Lower-cased substring match.
_COLD_TOKENS = ("-20", "-80", "4 °c", "4°c", "refriger", "frozen", "ice", "liquid nitrogen", "cryo")

# Categories that typically have long lead times for procurement.
_LEAD_CATEGORIES = {"cell_line", "organism"}


def _note(material: Material) -> Optional[dict]:
    """Return the FE's optional `note` chip. 'cold' takes precedence over
    'lead' when a material is both (e.g., a cell line stored in LN2)."""
    storage_blob = (material.storage or "").lower()
    if any(tok in storage_blob for tok in _COLD_TOKENS):
        return {"kind": "cold", "text": material.storage or "Requires cold-chain handling"}
    if material.category in _LEAD_CATEGORIES:
        return {"kind": "lead", "text": "Order well in advance — typical lead time 1-3 weeks"}
    return None


def _purpose(material: Material) -> str:
    """The FE always shows a purpose string. Equipment items have purpose
    populated by the roll-up agent; for reagents/consumables we fall back
    to the spec field, the storage hint, or an empty string."""
    if material.purpose:
        return material.purpose
    if material.spec:
        return material.spec
    return ""


def adapt_materials(materials: MaterialsOutput) -> FEMaterialsView:
    """Project a MaterialsOutput into the FE's grouped MaterialGroup shape.
    Empty groups are dropped. Within each group, items keep the order the
    roll-up agent emitted them (which usually tracks procedure order)."""
    by_cat: dict[str, list[FEReagent]] = {cat: [] for cat in _GROUP_ORDER}
    for m in materials.materials:
        if m.category not in by_cat:
            # Defensive — shouldn't happen since Pydantic constrains category
            continue
        by_cat[m.category].append(FEReagent(
            name=m.name,
            purpose=_purpose(m),
            # vendor/sku come from Stage 4 once that lands; placeholder for now.
            supplier=m.vendor or "TBD",
            catalog=m.sku or "TBD",
            qty=_qty_string(m),
            note=_note(m),
        ))

    groups: list[FEMaterialGroup] = []
    for cat in _GROUP_ORDER:
        items = by_cat[cat]
        if not items:
            continue
        label, description = _GROUP_LABELS[cat]
        groups.append(FEMaterialGroup(group=label, description=description, items=items))

    return FEMaterialsView(
        groups=groups,
        total_unique_items=materials.total_unique_items,
        gaps=list(materials.gaps),
    )
