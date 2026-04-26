# AI Scientist Assistant

From a scientific hypothesis to a runnable experiment plan.

A natural-language hypothesis goes in. Out comes a literature novelty check plus an operationally grounded plan: protocol steps with citations, materials, a timeline, validation criteria, and a reviewer-perspective design critique. Built for the [Hack-Nation × Fulcrum Science](https://hack-nation.ai/) Challenge 04.

> **Scope:** Bioscience only — biomedical and life-sciences experiments. Out-of-scope domains (climate, materials science, pure chemistry) are intentionally excluded so prompts, retrieval, and supplier coverage stay focused.

## How it works

Eight stages share a single `ExperimentPlan` document (blackboard pattern). Each stage reads the fields it needs and writes its result back. The UI subscribes to the plan and renders sections as they land.

| Stage | Source | Writes | Status |
|---|---|---|---|
| 1. Lit Review | Europe PMC | `lit_review` | ✅ shipped — multi-query rewrite (specific → broad), per-reference `key_differences` |
| 2. Protocol | protocols.io (live) | `protocol` | ✅ shipped — multi-agent (architect → writers → roll-up); researcher candidate selection |
| 3. Materials | protocols.io + Tavily + LLM | `materials` | ✅ shipped — every row enriched with supplier / catalog / price (verified or LLM estimate) |
| 4. Budget | derived from enriched materials | (FE compute) | ✅ shipped — real per-group USD subtotals on `/plan` from Tavily-cited prices, with explicit "(N/M priced)" honesty |
| 5. Timeline | derived from steps | `timeline` | ✅ shipped — deterministic, no LLM; partial / lower-bound estimates when coverage < 100% |
| 6. Validation | derived from protocol | `validation` | ✅ shipped — power calculation, controls, failure modes (citation-validated) |
| 7. Design Critique | LLM reviewer-perspective audit | `critique` | ✅ shipped — risks + confounders, every entry must cite a procedure / step / hypothesis field |
| 8. Summary | LLM final pass | `summary` | ⏳ pending |

Bonus features beyond the eight stages:
- **AI Assistant (`POST /chat` + `POST /chat/apply`)** — propose-then-apply chat over the experiment-plan blackboard. The LLM reads the plan JSON, proposes mutations as Apply / Reject cards (`update_protocol_step`, `add_material`, etc.), and applies them on click with a server-rendered diff that refreshes the affected page sections in place.
- **Researcher candidate-selection flow** — `POST /protocol-candidates` returns ranked protocols.io hits; the dedicated `/candidates` page lets the researcher review them with relevance scores + LLM rationale, multi-select up to 3, and leave freeform notes that thread into the architect + writer prompts.
- **Protocol PDF download** — formatted, citation-rich PDF rendered server-side ([`protocol_pipeline/pdf.py`](protocol_pipeline/pdf.py), `POST /protocol/pdf`).
- **Materials enrichment with confidence tiers** — `confidence: "verified"` (Tavily-cited supplier page, `Source ↗` link) vs `confidence: "estimate"` (LLM best-guess from training data, `BEST-GUESS (LLM)` chip). Every row gets either tier — never blank `TBD`.

Full architecture in [`spec/architecture.md`](spec/architecture.md). Type contracts in [`spec/TYPES.md`](spec/TYPES.md). On-disk layout and request lifecycle in [`technical_details.md`](technical_details.md).

## Features

A detailed inventory of what's actually live, organized by stage and surface.

### Stage 1 — Literature QC (`POST /lit-review`)
- **Multi-query rewrite**: LLM emits 1-3 ranked queries (specific → broad). Each runs against Europe PMC; results merged + de-duped by PMID/PMCID/DOI into a candidate pool of ~10-18 unique papers.
- **Europe PMC backed** — free, no auth, ~40M biomedical papers, structured authors / year / journal / abstract / DOI (no LLM hallucination of bibliographic fields).
- **Per-reference structured deltas** (`key_differences[]`): each cited paper carries 2-4 typed deltas (`subject` | `intervention` | `measurement` | `conditions` | `scope` | `method`) explaining how it differs from the user's hypothesis. Server-side parser drops malformed entries.
- **Surfaced query breadth** — FE shows "Searched on: [primary] · also tried: [broad₁] [broad₂]" chip row.
- **Novelty signal**: `novel` / `similar_work_exists` / `exact_match_found`.
- **24-hour cache** (down from 7 days) so iterative-hypothesis sessions don't return stale rankings.

### Stage 2 — Protocol (`POST /protocol`)
- **Live protocols.io fetch** via `protocols_client.py`. Falls back to offline samples when API is unreachable.
- **Multi-agent architecture**: relevance filter → architect (1 LLM call → outline of 3-8 procedures) → procedure writers (N parallel calls, one per procedure) → roll-up. Context isolation by procedure, so a 15-step protocol doesn't drift.
- **Researcher candidate-selection flow** (`POST /protocol-candidates`): dedicated `/candidates` page returns 5 ranked protocols.io hits with relevance scores, LLM rationale, language label, DOI link. Multi-select up to 3 + freeform notes textarea (1200 chars).
- **Three documented bug-fixes** against the upstream `protocols_client.py` layered inline (`order_field=relevance`, `payload` key, DraftJS step parsing).
- **Deviations from source** tracked per procedure with confidence + reason.
- **Per-procedure success criteria** (light-tier, distinct from Stage 6's experiment-wide criteria).

### Stage 3 — Materials (`POST /materials`)
- **Two-tier enrichment**:
  - **Verified**: Tavily search scoped to 7 supplier domains (Sigma, Thermo, Promega, Qiagen, IDT, ATCC, Addgene) + LLM extraction citing `source_url`. FE renders a `Source ↗` link.
  - **Estimate**: LLM best-guess from training data when Tavily returns nothing usable. FE renders a `BEST-GUESS (LLM)` chip with tooltip.
- **Force-backfill**: every row gets one tier — never blank `TBD`. Typical real-plan distribution: ~6 verified, ~22 estimate, **0 unenriched**.
- **Skip-list** for stationery / paperwork (`writing utensil`, `questionnaire`, etc.) catches non-procurable items at the gate so they get an LLM "(facility — not procurable)" marker instead of a fabricated SKU.
- **Price-targeted second-pass**: when supplier+catalog are known but price came back null, fires a focused `search_for_pricing` call with `include_raw_content=True` and runs regex-then-LLM extraction (substring-grounded).
- **Used-in cross-links**: each material lists which step IDs reference it ("Used in 1.3, 2.1").
- **Cross-currency safe**: budget compute excludes non-USD prices rather than implicitly converting.

### Stage 4 — Budget (FE compute)
- Real per-group USD subtotals on `/plan` derived from Tavily-cited material prices.
- Header chip flips between "Estimated cost" and "Partial cost (priced items only)".
- Per-row "(N/M priced)" chips so users see which groups are complete.
- Footer methodology line: `{N}/{M} items priced · pack-size pricing from supplier pages · USD only`.
- Falls back to hardcoded mock for design-demo mode.

### Stage 5 — Timeline (`POST /timeline`)
- **Deterministic** — pure ISO 8601 sum of step durations, no LLM call.
- **Two-tier durations**: `duration` (strict — null when any step missing/unparseable) + `partial_duration` (best-effort lower bound).
- **`≥ X (partial)` chips** when strict total is null but partial available — 80%-covered phases surface useful info instead of "INCOMPLETE".
- **Coverage chip** per phase reflects parseability, not just truthiness.
- **Methodology line** per phase: explains which steps were missing or non-conforming, names them when there are ≤3.
- **Linear pipeline** dependencies (each phase depends on previous); parallelization opportunities listed in `assumptions`.

### Stage 6 — Validation (`POST /validation`)
- **Closed-form power calculation**: `n = 2 * ((z_α/2 + z_β) / d)²` with formula text + every assumption (CV, mean range, distribution, α, power) shown on screen. Effect-size extraction is regex-based (deterministic).
- **Cohen's d conversion** for fold-change handles reductions correctly (`abs(log(...))` symmetric for 0.5x and 2x).
- **Aggregated success criteria** — experiment-wide criterion from `hypothesis.dependent` + per-procedure rolls.
- **Aggregated controls** with type classification (positive / negative / vehicle / sham).
- **Failure modes** (1 LLM call): each must cite a specific procedure or step. Parser validates citations against the procedure list and drops ungrounded entries.
- **Methodology footer** documents how the whole block was assembled.

### Stage 7 — Critique (`POST /critique`)
- **Risks + confounders** with citation enforcement: every entry must reference `procedure 'X'` / `step N (procedure 'X')` / `hypothesis.{field}`. Server-side parser validates against the protocol's procedure list; ungrounded entries are dropped.
- **Severity** (low/medium/high) + **category** (statistical / experimental / biological / technical / ethical / regulatory).
- **Deterministic recommendation** (`proceed` / `proceed_with_caution` / `revise_design`) — recomputed from the validated risk profile, not free-text LLM output. Visible verdict always matches visible risks.
- **Methodology** documents how the critique was produced (model + citation enforcement).

### AI Assistant Chat (`POST /chat` + `POST /chat/apply`)
- **Propose-then-apply** over the experiment-plan blackboard.
- **Schema-validated tool calls**: `update_protocol_step`, `add_material`, `update_material`, `remove_material`. Pre-validation drops bad calls (e.g. Gemini Flash stuffing the literal placeholder `"value"` into the `field` argument) before they render Apply cards.
- **Two response modes** in the system prompt:
  1. **Concrete edits** ("change step p1-s3 to 15 minutes") → use tools.
  2. **Suggestions / discussion** ("make it cooler") → prose with 2-4 plan-grounded specifics.
- **Useful prose for unknown-tool hallucinations** — when the LLM tries `update_hypothesis_*`, redirects the user to `/lab` instead of "Done. (Skipped …)".
- **Apply path** persists mutations + returns updated `frontend_views`. FE listens for `praxis:chat-applied` event and refreshes affected sections in place — no full re-fetch.
- **Page-aware tools**: read-only routes (`/lab`, `/literature`) get no mutator tools; only `/plan` does.

### Protocol PDF Export (`POST /protocol/pdf`)
- Server-side reportlab render. Pure Python — no headless browser.
- Title + hypothesis recap + per-procedure heading + numbered steps with body / duration / CRITICAL & PAUSE-POINT chips / equipment / reagents / todos / anticipated outcome / troubleshooting + success criteria + deviations + cited protocols + assumptions + UTC-timestamped footer.
- Slugged filename per `experiment_type` (e.g. `protocol-cryopreservation-comparison.pdf`).
- W/M/Y-aware ISO-8601 duration formatter so multi-week protocols render real times.

### UX / Frontend
- **Six-page flow**: `/lab` → `/literature` → `/candidates` → `/plan` → `/review` → `/drafts`/`/library`/`/account`.
- **Floating AI Assistant launcher** on every page; route-aware suggestions panel.
- **Hypothesis recap card** on `/plan` from real router state (composed deterministically from structured fields when `research_question` is empty).
- **Color-coded hypothesis tokens** on `/literature` (subject = ink-blue, variable = sage, condition = amber).
- **Procedure-grouped protocol view** with running-clock chip per step (`t = 5m, t = 12m...`).
- **Per-reference accordion** on the "Where your work diverges" section — collapsed by default, summary line shows count + dimension chips.
- **Materials cards** show source link or `BEST-GUESS` chip.
- **Timeline phases** with coverage chips, methodology lines, back-links to source procedures.
- **Validation panel** with power-calc card, controls list, failure-modes block, recommendation chip.
- **Mock-fallback everywhere** so direct page navigation still demos the design without a backend.

### Backend infrastructure
- **Blackboard pattern**: shared `ExperimentPlan` JSON document persisted under `plans/`. Every endpoint reads + writes the same blackboard.
- **Status tracking**: `running` / `complete` / `failed` per stage with timestamps + error capture.
- **Two LLM providers** swappable via `LLM_PROVIDER`: OpenRouter → Gemini 2.5 Flash for dev (~$0.001/run), Anthropic → Claude Sonnet 4.6 for production with prompt caching.
- **File cache** (`src/lib/cache.py`) per source: 24h lit-review, 30-day catalog, 24h pricing.
- **Parallel fan-out**: `ThreadPoolExecutor` for protocol writers (5 workers), materials enrichment (6 workers), per-candidate step fetches (8 workers), `selected_protocol_ids` rehydration (10 workers).
- **Time-bound enrichment**: `/materials` returns even when Tavily hangs on one item.
- **Module logger** for non-fatal background work (enrichment failures route through standard logging, not raw stderr).
- **Sanitized 500s**: full traceback server-side, no internal details leaked to client.
- **Vercel-ready FE**: `VITE_API_BASE` for production API origin; SPA rewrites; root build config.

### Defensibility patterns (used everywhere)
- **`source_url` required** for verified materials enrichment.
- **`cites` required** for failure modes + risks + confounders + key_differences. Parser validates against known procedure / hypothesis-field set; ungrounded entries dropped.
- **Closed-form formulas** preferred over LLM for any numeric output (timeline sums, power calc, recommendation derivation).
- **Two-tier honesty** wherever an LLM might guess: verified vs estimate, strict vs partial, complete vs incomplete-with-explicit-coverage.
- **Methodology surfaced in the data** (not just the prompt) so the audit trail travels with the JSON.
- **Conservative-by-design**: partial sums show as `≥ X` rather than asserted totals; missing data renders as `—` rather than fabricated.

## Stack

React + Vite + Tailwind (Lovable scaffold) → Vercel · Flask API · protocols.io REST API (live via [`protocols_client.py`](protocols_client.py)) · Europe PMC · Tavily.

**LLM:** OpenRouter → Gemini 2.5 Flash for **prototyping** (cheap dev iteration), Anthropic direct → Claude Sonnet 4.6 for **production** (demo / quality-sensitive runs, with prompt caching). Switch via `LLM_PROVIDER` in `.env`.

**Plan storage:** JSON files under `plans/` (gitignored). The blackboard is one inspectable document per run. Supabase + pgvector remains the planned upgrade for shared state and embedding search; not in use yet.

## Running it locally

Two terminals:

```bash
# Terminal 1 — Flask backend
python -m flask --app app run --port 5000

# Terminal 2 — Vite frontend
cd frontend && npm install && npm run dev
```

Then visit http://localhost:8080/lab, fill in a hypothesis, walk through `/literature` → `/plan`. End-to-end run is roughly 50–60 s on Gemini Flash.

For Stage 1 alone, a CLI runner is included:

```bash
python run_lr.py inputs/crp.yaml          # one sample
python run_protocol.py inputs/crp.yaml    # Stages 2-3 against the same input
```

Detailed walk-through in [`HOWTO.md`](HOWTO.md).

## Working on this

- **Implementing a stage?** Read the relevant section in [`spec/TYPES.md`](spec/TYPES.md), then open the matching file in [`spec/types/`](spec/types/). The "Stages at a glance" matrix near the top of TYPES.md tells you exactly which fields your stage reads and writes.
- **Orienting?** Read [`spec/architecture.md`](spec/architecture.md) — ~15 min, includes the system diagram. For the on-disk layout, see [`technical_details.md`](technical_details.md).
- **Need a type?** All public types are re-exported from [`spec/types/index.ts`](spec/types/index.ts). Import like `import type { ExperimentPlan } from '@/spec/types'`.
- **Adding a new stage?** Add a new file under `spec/types/`, register a `StageContract` in `spec/types/stage-contracts.ts`, document in `spec/TYPES.md`, then drop a new module under `protocol_pipeline/` (or a sibling pipeline package) plus a Flask endpoint in `app.py`.

## Status

Seven of the eight planned stages ship today (Stages 1, 2, 3, 5, 6, 7 plus a real per-group budget computed on the FE from the Tavily-enriched materials prices). Stage 8 (Summary) is the remaining gap — type contract is in `spec/types/`. Bonus features beyond the spec: AI Assistant chat over the plan blackboard, researcher candidate-selection flow, and a server-rendered protocol PDF. The frontend is fully wired to the live API; mock-fallbacks remain in place so the design demo still runs without a backend.
