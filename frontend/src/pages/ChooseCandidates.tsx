/**
 * Researcher-in-the-loop candidate selection.
 *
 * Sits between /literature and /plan. Fetches up to 5 candidate
 * protocols from protocols.io via POST /protocol-candidates, lets
 * the researcher pick 1-3 + leave freeform notes, then forwards the
 * selection to /plan as router state. ExperimentPlan reads
 * `selected_protocol_ids` + `researcher_notes` and passes them
 * verbatim to POST /protocol.
 *
 * Two exit paths:
 *   - "Use these for the protocol" (1-3 selected) → /plan with selection
 *   - "Synthesize without specific sources" (0 selected) → /plan
 *     (BE then auto-runs its ranked search)
 *
 * Defensibility:
 *   - The exact `query_used` and `queries_tried` from the BE are
 *     surfaced so the user knows what was searched.
 *   - Each card shows the protocols.io DOI / URL — researcher can
 *     verify the underlying source before picking.
 *   - The relevance score + reason came from the LLM's editorial
 *     pass, not a substring match — explicit so the user knows
 *     what's grounding the ranking.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  postProtocolCandidates,
  type ProtocolCandidate,
  type StructuredHypothesis,
} from "@/lib/api";
import {
  ArrowRight,
  Check,
  ChevronDown,
  ExternalLink,
  FlaskConical,
  Info,
} from "lucide-react";
import { Button } from "@/components/ui/button";

// Hard limit per BE expectation — picking more than 3 makes the
// procedure-writer prompts cluttered and the relevance signal noisy.
const MAX_PICKS = 3;

const LANGUAGE_LABELS: Record<string, string> = {
  en: "English",
  es: "Spanish",
  fr: "French",
  de: "German",
  pt: "Portuguese",
  it: "Italian",
  ja: "Japanese",
  ko: "Korean",
  zh: "Chinese",
};

function languageLabel(code: string): string {
  const k = (code || "").toLowerCase();
  return LANGUAGE_LABELS[k] || k.toUpperCase() || "Unknown";
}

function relevanceBarTone(score: number): string {
  if (score >= 0.7) return "bg-primary";
  if (score >= 0.5) return "bg-sage";
  return "bg-[hsl(38_70%_45%)]";
}

const ChooseCandidates = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const navState = (location.state as {
    plan_id?: string;
    structured?: StructuredHypothesis;
  } | null) ?? null;
  const incomingPlanId = navState?.plan_id;
  const incomingStructured = navState?.structured;

  // Page state
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<ProtocolCandidate[]>([]);
  const [queryUsed, setQueryUsed] = useState<string>("");
  const [queriesTried, setQueriesTried] = useState<string[]>([]);
  const [planId, setPlanId] = useState<string | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [notes, setNotes] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Mock-only mode: user navigated here directly without router state.
  // We don't have anything to send the BE, so show a hint + a "skip
  // to plan" link; the design demo still renders.
  const useMockData = !incomingPlanId && !incomingStructured;

  useEffect(() => {
    if (useMockData) {
      setLoading(false);
      return;
    }

    const ac = new AbortController();
    const body = incomingPlanId
      ? { plan_id: incomingPlanId }
      : { structured: incomingStructured! };

    setLoading(true);
    setError(null);

    (async () => {
      try {
        const res = await postProtocolCandidates(body, ac.signal);
        setCandidates(res.candidates);
        setQueryUsed(res.query_used || "");
        setQueriesTried(res.queries_tried || []);
        setPlanId(res.plan_id);
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Couldn't load candidate protocols.",
        );
      } finally {
        setLoading(false);
      }
    })();

    return () => ac.abort();
  }, [incomingPlanId, incomingStructured, useMockData]);

  const toggleSelected = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < MAX_PICKS) {
        next.add(id);
      }
      return next;
    });
  };

  const handleProceed = (withSelection: boolean) => {
    const usePlanId = planId ?? incomingPlanId ?? null;
    navigate("/plan", {
      state: {
        plan_id: usePlanId,
        structured: incomingStructured,
        // Only pass selection fields when the user actually picked
        // something — the BE skips its own ranked search when these
        // are present, so passing an empty array would force the
        // pipeline into "no sources" mode.
        selected_protocol_ids: withSelection
          ? Array.from(selectedIds)
          : undefined,
        // Notes go through on BOTH paths: even when the researcher
        // skips the candidate selection, they may want to leave
        // general guidance ("focus on post-thaw viability, ignore
        // the freezing rate"). The BE prompt accepts notes
        // independently of selected_protocol_ids.
        researcher_notes: notes.trim() || undefined,
      },
    });
  };

  const selectionCount = selectedIds.size;
  const canProceed = selectionCount > 0 && selectionCount <= MAX_PICKS;

  const sortedCandidates = useMemo(
    () =>
      [...candidates].sort(
        (a, b) => (b.relevance_score ?? 0) - (a.relevance_score ?? 0),
      ),
    [candidates],
  );

  return (
    <div className="relative min-h-screen bg-paper text-ink">
      <div aria-hidden className="pointer-events-none absolute inset-0 lab-grid" />

      <main className="relative mx-auto max-w-5xl px-6 py-12 sm:px-10 sm:py-16">
        <header className="mb-10">
          <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
            Step 03 · Choose source protocols
          </p>
          <h1 className="mt-2 font-serif-display text-[42px] leading-tight text-ink">
            Pick the protocols this experiment should be grounded on.
          </h1>
          <p
            className="mt-3 max-w-3xl text-[18px] leading-[1.6] text-ink-soft"
            style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
          >
            We searched protocols.io and ranked the most relevant matches.
            Pick up to {MAX_PICKS} you'd like the assistant to draw from —
            or skip and let the synthesis use whatever it finds.
          </p>

          {(queryUsed || queriesTried.length > 0) && !useMockData && (
            <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-sm border border-rule bg-paper-raised px-4 py-2.5 font-mono-notebook text-[11px] uppercase tracking-[0.18em] text-ink-soft">
              <span className="text-muted-foreground">Searched on</span>
              {queryUsed && (
                <span className="rounded-sm border border-primary/30 bg-primary/[0.06] px-2 py-0.5 text-primary">
                  {queryUsed}
                </span>
              )}
              {queriesTried.length > 1 && (
                <>
                  <span className="text-muted-foreground/70">tried:</span>
                  {queriesTried.map((q) => (
                    <span
                      key={q}
                      className={
                        "rounded-sm border px-2 py-0.5 " +
                        (q === queryUsed
                          ? "hidden"
                          : "border-rule bg-paper text-muted-foreground")
                      }
                    >
                      {q}
                    </span>
                  ))}
                </>
              )}
            </div>
          )}
        </header>

        {/* Error / empty / loading states */}
        {loading && (
          <div className="mb-10 rounded-md border border-rule bg-paper-raised px-7 py-10 text-center">
            <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
              Searching protocols.io…
            </p>
            <p className="mt-2 text-[14px] text-ink-soft">
              Ranking matches by relevance. This usually takes 5–15 seconds.
            </p>
          </div>
        )}

        {!loading && error && (
          <div className="mb-10 rounded-md border border-destructive/30 bg-destructive/[0.05] px-7 py-5">
            <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-destructive">
              Couldn't load candidates
            </p>
            <p className="mt-2 text-[14px] text-ink">{error}</p>
            <p className="mt-3 text-[13px] text-ink-soft">
              You can still proceed — the assistant will fall back to its
              built-in protocol library.
            </p>
          </div>
        )}

        {!loading && !error && useMockData && (
          <div className="mb-10 rounded-md border border-rule bg-paper-raised px-7 py-6">
            <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
              Demo mode
            </p>
            <p className="mt-2 text-[14px] text-ink">
              No hypothesis in the page state — start from{" "}
              <Link to="/lab" className="border-b border-ink text-ink hover:border-primary hover:text-primary">
                /lab
              </Link>{" "}
              to fetch real candidates, or skip straight to the plan to see
              the rest of the flow on mock data.
            </p>
          </div>
        )}

        {!loading && !error && !useMockData && sortedCandidates.length === 0 && (
          <div className="mb-10 rounded-md border border-rule bg-paper-raised px-7 py-6">
            <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
              No matches
            </p>
            <p className="mt-2 text-[14px] text-ink">
              protocols.io returned nothing for any of the queries we tried.
              The synthesis will draw from the assistant's built-in library
              instead.
            </p>
          </div>
        )}

        {/* Candidates list */}
        {!loading && sortedCandidates.length > 0 && (
          <ol className="mb-8 space-y-4">
            {sortedCandidates.map((c, i) => {
              const isSelected = selectedIds.has(c.id);
              const isOpen = expandedId === c.id;
              const pct = Math.round((c.relevance_score ?? 0) * 100);
              const tone = relevanceBarTone(c.relevance_score ?? 0);
              const disabled = !isSelected && selectionCount >= MAX_PICKS;
              return (
                <li
                  key={c.id}
                  className={
                    "group/card relative rounded-md border bg-paper-raised transition-all " +
                    (isSelected
                      ? "border-primary shadow-[0_4px_18px_-10px_hsl(var(--primary)/0.5)]"
                      : "border-rule hover:border-ink/30 hover:bg-rule-soft/20") +
                    (disabled ? " opacity-60" : "")
                  }
                >
                  <span
                    aria-hidden
                    className={
                      "absolute inset-y-0 left-0 w-[3px] rounded-l-md transition-colors " +
                      (isSelected ? tone : "bg-transparent")
                    }
                  />

                  <div className="grid grid-cols-1 gap-4 px-7 py-5 sm:grid-cols-[auto_1fr_auto] sm:items-start">
                    {/* Selection checkbox */}
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={isSelected}
                      aria-label={`Select ${c.title}`}
                      disabled={disabled}
                      onClick={() => toggleSelected(c.id)}
                      className={
                        "mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border-2 transition-all disabled:cursor-not-allowed " +
                        (isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-rule bg-paper hover:border-ink")
                      }
                    >
                      {isSelected && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
                    </button>

                    {/* Body */}
                    <div className="min-w-0">
                      <div className="flex items-baseline gap-3">
                        <span className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                          {String(i + 1).padStart(2, "0")}
                        </span>
                        <h3 className="font-serif-card text-[20px] leading-[1.3] text-ink">
                          {c.title || "(untitled protocol)"}
                        </h3>
                      </div>

                      <p className="mt-1.5 pl-[2.1rem] font-mono-notebook text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                        protocols.io · id {c.id} · {c.step_count} steps · {languageLabel(c.language)}
                        {c.doi && (
                          <>
                            {" "}
                            ·{" "}
                            <a
                              href={`https://doi.org/${c.doi}`}
                              target="_blank"
                              rel="noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="border-b border-transparent text-ink-soft hover:border-ink hover:text-ink"
                            >
                              doi
                            </a>
                          </>
                        )}
                      </p>

                      {c.description && (
                        <p
                          className={
                            "mt-3 pl-[2.1rem] text-[14.5px] leading-[1.6] text-ink-soft transition-all " +
                            (isOpen ? "" : "line-clamp-3")
                          }
                        >
                          {c.description}
                        </p>
                      )}

                      <div className="mt-3 flex flex-wrap items-center gap-3 pl-[2.1rem]">
                        <button
                          type="button"
                          onClick={() => setExpandedId(isOpen ? null : c.id)}
                          className="inline-flex items-center gap-1.5 font-mono-notebook text-[10px] uppercase tracking-[0.22em] text-muted-foreground transition-colors hover:text-ink"
                        >
                          {isOpen ? "Show less" : "Show full description"}
                          <ChevronDown
                            className={
                              "h-3 w-3 transition-transform " +
                              (isOpen ? "rotate-180" : "")
                            }
                            strokeWidth={1.75}
                          />
                        </button>
                        {c.url && (
                          <a
                            href={c.url}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex items-center gap-1.5 font-mono-notebook text-[10px] uppercase tracking-[0.22em] text-muted-foreground transition-colors hover:text-ink"
                          >
                            Open on protocols.io
                            <ExternalLink className="h-3 w-3" strokeWidth={1.75} />
                          </a>
                        )}
                      </div>

                      {/* Relevance reason — keeps the AI ranking auditable */}
                      <div className="mt-4 ml-[2.1rem] rounded-sm border border-rule bg-paper px-4 py-3">
                        <p className="font-mono-notebook text-[10px] uppercase tracking-[0.22em] text-sage">
                          Why this matched
                        </p>
                        <p className="mt-1.5 text-[13.5px] leading-[1.55] text-ink-soft">
                          {c.relevance_reason || "(no rationale provided)"}
                        </p>
                      </div>
                    </div>

                    {/* Relevance score */}
                    <div className="flex flex-col items-start sm:items-end">
                      <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                        Relevance
                      </p>
                      <p
                        className="mt-1 text-[28px] italic leading-none text-ink"
                        style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
                      >
                        {pct}%
                      </p>
                      <div className="mt-2 h-[3px] w-24 overflow-hidden rounded-sm bg-rule-soft/70">
                        <div
                          className={"h-full origin-left transition-transform duration-700 ease-out " + tone}
                          style={{
                            width: "100%",
                            transform: `scaleX(${c.relevance_score ?? 0})`,
                          }}
                        />
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}

        {/* Researcher notes */}
        {!loading && (sortedCandidates.length > 0 || useMockData) && (
          <section className="mb-8 rounded-md border border-rule bg-paper-raised px-7 py-6">
            <div className="flex items-baseline justify-between">
              {/* htmlFor pairs the heading with the textarea so screen
                  readers announce them together; the cursor: pointer
                  on hover gives sighted users the same affordance. */}
              <label
                htmlFor="researcher-notes"
                className="cursor-pointer font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-sage"
              >
                Researcher notes (optional)
              </label>
              <span className="font-mono-notebook text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                threaded into the architect &amp; writer prompts
              </span>
            </div>
            <p className="mt-2 text-[13px] leading-[1.55] text-ink-soft">
              Anything the assistant should know — e.g. constraints
              (&ldquo;we have a Sorvall RC-6, not Beckman&rdquo;), preferences
              (&ldquo;use trehalose, not DMSO&rdquo;), or focus (&ldquo;don&rsquo;t
              bother with the freezing rate; we care about post-thaw viability&rdquo;).
            </p>
            <textarea
              id="researcher-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional: tell the assistant what matters and what to skip…"
              rows={4}
              maxLength={1200}
              className="mt-3 w-full rounded-sm border border-rule bg-paper px-3 py-2 font-mono-notebook text-[13px] leading-[1.5] text-ink placeholder:text-muted-foreground focus:border-ink focus:outline-none focus:ring-0"
            />
            <p className="mt-1 text-right font-mono-notebook text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              {notes.length} / 1200
            </p>
          </section>
        )}

        {/* CTA / footer */}
        <section
          aria-label="Continue"
          className="relative overflow-hidden rounded-md border border-primary/40 bg-paper-raised shadow-[0_1px_0_hsl(var(--primary)/0.15),0_24px_60px_-30px_hsl(var(--primary)/0.35)]"
        >
          <div aria-hidden className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-rule to-transparent" />
          <div className="grid grid-cols-1 gap-0 sm:grid-cols-[1fr_auto_1fr]">
            <div className="px-7 py-7 sm:px-9 sm:py-8">
              <div className="flex items-center gap-3">
                <span className="flex h-7 w-7 items-center justify-center rounded-full border border-primary bg-primary text-primary-foreground">
                  <Check className="h-3.5 w-3.5" strokeWidth={2.5} />
                </span>
                <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
                  Step 03 · Source protocols
                </p>
              </div>
              <h3 className="mt-4 font-serif-display text-[26px] leading-tight text-ink">
                {selectionCount === 0
                  ? "No protocols selected yet"
                  : `${selectionCount} of ${MAX_PICKS} selected`}
              </h3>
              <p className="mt-2 text-[14px] leading-[1.65] text-ink-soft">
                {selectionCount === 0
                  ? "Pick 1–3 candidates to ground the protocol on — or skip and let the assistant choose for you."
                  : "Add notes above if you want to steer the synthesis, then continue to the plan."}
              </p>
            </div>

            <div
              className="flex items-center justify-center border-rule px-6 py-2 sm:border-x sm:px-8 sm:py-9"
              aria-hidden
            >
              <div className="flex flex-col items-center gap-2 sm:gap-3">
                <span className="hidden h-8 w-px bg-rule sm:block" />
                <ArrowRight className="h-5 w-5 text-primary" strokeWidth={1.75} />
                <span className="hidden h-8 w-px bg-rule sm:block" />
              </div>
            </div>

            <div className="flex flex-col items-stretch gap-3 px-7 py-7 sm:items-end sm:px-9 sm:py-8">
              <Button
                disabled={!canProceed}
                onClick={() => handleProceed(true)}
                className="group h-14 gap-3 rounded-sm bg-ink px-7 text-[15px] font-medium text-paper shadow-[0_8px_24px_-12px_hsl(var(--ink)/0.6)] transition-all hover:bg-ink/90 hover:shadow-[0_10px_28px_-10px_hsl(var(--ink)/0.7)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="font-mono-notebook text-[10px] uppercase tracking-[0.24em] opacity-70">
                  Step 04 →
                </span>
                <span className="font-serif-display text-[19px] italic">
                  Use these for the protocol
                </span>
                <ArrowRight
                  className="h-5 w-5 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.75}
                />
              </Button>
              <button
                type="button"
                onClick={() => handleProceed(false)}
                className="group inline-flex items-center justify-center gap-1.5 self-stretch border-b border-transparent pb-0.5 font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-ink-soft transition-colors hover:border-ink hover:text-ink sm:self-end"
              >
                Skip — synthesize without specific sources
                <ArrowRight
                  className="h-3 w-3 transition-transform group-hover:translate-x-0.5"
                  strokeWidth={1.75}
                />
              </button>
            </div>
          </div>
        </section>

        {/* Help / context */}
        {!useMockData && sortedCandidates.length > 0 && (
          <p className="mt-6 inline-flex items-start gap-2 text-[12px] leading-[1.55] text-muted-foreground">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
            <span>
              Selected candidates skip the BE's own ranked search and ground
              the protocol on exactly what you picked. Skipping lets the
              assistant run its full search; either path works.
            </span>
          </p>
        )}

        {/* Subtle decorative motif so the page doesn't look bare while
            candidates are loading */}
        <FlaskConical
          aria-hidden
          className="pointer-events-none absolute right-10 top-12 hidden h-32 w-32 text-ink opacity-[0.04] sm:block"
          strokeWidth={1}
        />
      </main>
    </div>
  );
};

export default ChooseCandidates;
