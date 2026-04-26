import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, FlaskConical, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  postProtocolSources,
  type ProtocolSourceCard,
  type ProtocolSourcesFetchMode,
  type StructuredHypothesis,
} from "@/lib/api";

export type ProtocolPreferencesState = {
  lowCost: boolean;
  highAccuracy: boolean;
  fastExecution: boolean;
  constraints: string;
};

export type SelectedProtocolInfo = {
  id: string;
  title: string;
  source: string;
};

export type ProtocolSourceSelection = {
  selected: SelectedProtocolInfo[];
  preferences: ProtocolPreferencesState;
};

type MockProtocol = ProtocolSourceCard;

const PREF_TOGGLE_SUB = {
  lowCost: "Reduces reagent and equipment cost",
  highAccuracy: "Adds replicates and tighter controls",
  fastExecution: "Simplifies protocol and reduces runtime",
} as const;

const PREF_ACTIVE_SUMMARY = {
  lowCost: "Low cost — will favor cheaper reagents and equipment",
  highAccuracy: "High accuracy — will increase replicates and tighter controls",
  fastExecution: "Fast execution — will simplify steps and shorten runtime",
} as const;

function contextBlob(
  structured: StructuredHypothesis | undefined,
  displayList: ProtocolSourceCard[],
): string {
  const parts: string[] = [];
  if (structured) {
    parts.push(JSON.stringify(structured));
  }
  for (const p of displayList) {
    parts.push(
      [p.title, p.summary, ...(p.keySteps ?? []), p.citation ?? ""].join(" "),
    );
  }
  return parts.join(" ").toLowerCase();
}

const MOCK_PROTOCOLS: MockProtocol[] = [
  {
    id: "pio-1",
    title: "Glucose-limited chemostat for steady-state E. coli growth",
    source: "protocols.io",
    citation:
      "Lee & Monod (2021). “Glucose-limited chemostat for steady-state E. coli growth.” protocols.io. https://doi.org/10.17504/example.pio.chemostat",
    summary:
      "A continuous-culture method for holding dilution rate and residual substrate constant, intended for reliable measurement of specific growth rate under well-defined selection pressure.",
    keySteps: [
      "Purge and calibrate chemostat vessels and pumps; verify sterile boundary.",
      "Establish batch phase to OD target, then begin feed at fixed dilution D.",
      "Sample effluent and biomass in steady state before kinetic measurements.",
    ],
  },
  {
    id: "pio-2",
    title: "OD600 kinetics in 96-well plate with metabolic acclimation",
    source: "protocols.io",
    citation:
      "Vance et al. (2019). “OD600 kinetics in 96-well plate with metabolic acclimation.” protocols.io. https://doi.org/10.17504/example.pio.od600",
    summary:
      "High-throughput growth curves with a brief carbon-free wash to reduce carryover, followed by a glucose gradient in minimal medium for resolving catabolite-repression–sensitive windows.",
    keySteps: [
      "Inoculate from mid-log shake flask; normalize inoculum density.",
      "Two gentle washes in carbon-free M9; dispense 200 µL per well in gradient layout.",
      "Orbitally shake, read every 5–7 min, fit log-phase slope for growth rate.",
    ],
  },
  {
    id: "jo-1",
    title: "M9 batch validation of diauxic shift timing",
    source: "Journal of Bacteriology (methods supplement)",
    citation:
      "Okamoto & Jensen (2018). “M9 batch validation of diauxic shift timing.” J. Bacteriol. methods supplement. https://doi.org/10.1128/example.jb.methods",
    summary:
      "A batch protocol focused on replicates and controls when comparing lag phase and second-growth re-entry after a carbon switch; emphasizes baseline glucose exhaustion timing.",
    keySteps: [
      "Time-course sampling for residual glucose and acetate.",
      "Parallel uninduced and induced controls in identical media.",
      "OD and metabolite time alignment for shift annotation.",
    ],
  },
];

type LocationState = {
  plan_id?: string;
  structured?: StructuredHypothesis;
  domain?: string;
  protocolSourceSelection?: ProtocolSourceSelection;
} | null;

const defaultPrefs = (): ProtocolPreferencesState => ({
  lowCost: false,
  highAccuracy: true,
  fastExecution: false,
  constraints: "",
});

const ProtocolSources = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const nav = (location.state as LocationState) ?? null;

  const [prefs, setPrefs] = useState<ProtocolPreferencesState>(() => {
    if (nav?.protocolSourceSelection?.preferences) {
      const raw = { ...defaultPrefs(), ...nav.protocolSourceSelection.preferences } as Record<
        string,
        unknown
      >;
      delete raw.method;
      return raw as ProtocolPreferencesState;
    }
    return defaultPrefs();
  });

  const [useProtocol, setUseProtocol] = useState<Record<string, boolean>>(() => {
    if (nav?.protocolSourceSelection?.selected?.length) {
      const sel = new Set(nav.protocolSourceSelection.selected.map((s) => s.id));
      return Object.fromEntries(MOCK_PROTOCOLS.map((m) => [m.id, sel.has(m.id)]));
    }
    return {
      "pio-1": true,
      "pio-2": true,
      "jo-1": false,
    };
  });

  const [protocols, setProtocols] = useState<ProtocolSourceCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [fetchMode, setFetchMode] = useState<ProtocolSourcesFetchMode | "">("");

  const hasStructured = Boolean(
    nav?.structured &&
      Object.values(nav.structured).some(
        (v) => typeof v === "string" && v.trim().length > 0,
      ),
  );

  useEffect(() => {
    const ac = new AbortController();
    (async () => {
      setLoading(true);
      try {
        const res = await postProtocolSources(
          { structured: nav?.structured },
          ac.signal,
        );
        if (Array.isArray(res.sources)) {
          setProtocols(res.sources);
        } else {
          setProtocols([]);
        }
        if (typeof res.search_query === "string") {
          setSearchQuery(res.search_query);
        } else {
          setSearchQuery("");
        }
        if (typeof res.fetch_mode === "string") {
          setFetchMode(res.fetch_mode as ProtocolSourcesFetchMode);
        } else {
          setFetchMode("");
        }
      } catch {
        setProtocols([]);
        setSearchQuery("");
        setFetchMode("error");
      } finally {
        if (!ac.signal.aborted) {
          setLoading(false);
        }
      }
    })();
    return () => ac.abort();
  }, [nav?.structured]);

  useEffect(() => {
    if (protocols.length === 0) return;
    setUseProtocol((prev) => {
      const next = { ...prev };
      protocols.forEach((p, i) => {
        if (next[p.id] === undefined) {
          next[p.id] = i < 2;
        }
      });
      return next;
    });
  }, [protocols]);

  // With a real hypothesis, do not swap in the E. coli / demo mocks on failure.
  const displayList = useMemo((): ProtocolSourceCard[] => {
    if (protocols.length > 0) return protocols;
    if (hasStructured) return [];
    return MOCK_PROTOCOLS;
  }, [protocols, hasStructured]);

  useEffect(() => {
    if (loading) return;
    if (nav?.protocolSourceSelection?.preferences) return;
    if (!nav?.structured && displayList.length === 0) return;

    const t = contextBlob(nav?.structured, displayList);

    setPrefs((prev) => {
      const next = { ...prev };
      if (/\b(growth|activity)\b/.test(t)) {
        next.highAccuracy = true;
      }
      if (
        t.includes("gradient") ||
        t.includes("96-well") ||
        t.includes("96 well") ||
        t.includes("microplate") ||
        t.includes("well plate")
      ) {
        next.fastExecution = true;
      }
      return next;
    });
  }, [loading, nav?.protocolSourceSelection?.preferences, nav?.structured, displayList]);

  const selectedList = useMemo((): SelectedProtocolInfo[] => {
    return displayList.filter((m) => useProtocol[m.id]).map((m) => ({
      id: m.id,
      title: m.title,
      source: m.source,
    }));
  }, [displayList, useProtocol]);

  const activePreferenceSummaryLines = useMemo(() => {
    const out: string[] = [];
    if (prefs.lowCost) out.push(PREF_ACTIVE_SUMMARY.lowCost);
    if (prefs.highAccuracy) out.push(PREF_ACTIVE_SUMMARY.highAccuracy);
    if (prefs.fastExecution) out.push(PREF_ACTIVE_SUMMARY.fastExecution);
    return out;
  }, [prefs]);

  const goBack = () => {
    navigate("/literature", {
      state: {
        plan_id: nav?.plan_id,
        structured: nav?.structured,
        domain: nav?.domain,
      },
    });
  };

  return (
    <div className="min-h-dvh text-ink">
      <header className="relative border-b border-rule">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 sm:px-10">
          <Link to="/" className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-7 w-7 items-center justify-center rounded-sm border border-rule bg-paper-raised"
            >
              <FlaskConical className="h-4 w-4 text-primary" strokeWidth={1.5} />
            </span>
            <span className="font-serif-display text-xl tracking-tight text-ink">
              Praxis
            </span>
          </Link>
          <nav className="hidden items-center gap-7 text-sm text-muted-foreground sm:flex">
            <Link className="transition-colors hover:text-ink" to="/drafts">
              Drafts
            </Link>
            <Link className="transition-colors hover:text-ink" to="/library">
              Library
            </Link>
            <Link className="transition-colors hover:text-ink" to="/account">
              Account
            </Link>
          </nav>
        </div>
      </header>

      <main className="relative mx-auto max-w-5xl px-6 pb-24 pt-12 sm:px-10 sm:pt-16">
        <section aria-labelledby="protocol-sources-title" className="mb-12">
          <p className="font-mono-notebook text-[13px] uppercase tracking-[0.22em] text-muted-foreground">
            <span className="text-primary">●</span>&nbsp;&nbsp;Step{" "}
            <span className="text-ink">03</span> of 05 — Protocol sources
          </p>
          <h1
            id="protocol-sources-title"
            className="mt-5 font-serif-display text-[44px] leading-[1.04] text-ink sm:text-[60px]"
          >
            How should this experiment be run?
          </h1>
          <p className="mt-6 max-w-2xl text-[17px] leading-[1.7] text-ink-soft">
            Select relevant prior protocols and define constraints before generating a
            plan.
          </p>
        </section>

        {/* Section 1 — Retrieved Protocols */}
        <section aria-labelledby="retrieved-title" className="mb-12">
          <div className="mb-4 flex items-baseline justify-between gap-4">
            <h2
              id="retrieved-title"
              className="font-serif-display text-[26px] leading-tight text-ink"
            >
              Retrieved protocols
            </h2>
            <p className="font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-muted-foreground">
              {loading ? "…" : displayList.length} found · select any that apply
            </p>
          </div>

          {loading && (
            <section className="mb-10 rounded-md border border-rule bg-paper-raised px-7 py-6">
              <p className="font-serif-display text-[20px] text-ink">Searching protocols…</p>

              <div className="mt-4 h-[3px] w-full bg-rule-soft/70 overflow-hidden rounded-sm">
                <div className="h-full w-1/3 bg-sage animate-pulse" />
              </div>

              <ul className="mt-4 flex gap-3">
                {["protocols.io", "methods", "literature"].map((s) => (
                  <li
                    key={s}
                    className="px-2 py-1 text-[11px] uppercase tracking-[0.2em] border border-rule rounded-sm text-ink-soft animate-pulse"
                  >
                    {s}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {!loading &&
            hasStructured &&
            displayList.length > 0 &&
            fetchMode === "publications_fallback" && (
              <p
                className="mb-4 max-w-2xl text-[15px] leading-[1.65] text-ink-soft"
                role="status"
              >
                No keyword search hits on protocols.io
                {searchQuery ? ` for “${searchQuery}”` : ""}. Showing the latest
                public protocols instead; pick any that are useful or leave unchecked.
              </p>
            )}

          {!loading && hasStructured && displayList.length === 0 && (
            <p
              className="mb-4 max-w-2xl text-[15px] leading-[1.65] text-ink-soft"
              role="status"
            >
              {fetchMode === "missing_credentials" ? (
                <>
                  Add{" "}
                  <span className="font-mono-notebook text-[11px] uppercase tracking-[0.12em]">
                    PROTOCOLS_IO_API_KEY
                  </span>{" "}
                  (or <span className="font-mono-notebook text-[11px] uppercase tracking-[0.12em]">PROTOCOLS_IO_TOKEN</span>)
                  in your{" "}
                  <span className="font-mono-notebook text-[11px] uppercase tracking-[0.12em]">.env</span>
                  , restart the API server, and reload.
                </>
              ) : (
                <>
                  Could not load protocols from protocols.io
                  {searchQuery ? ` (search: “${searchQuery}”)` : ""}. Check that the
                  Flask server can reach the internet, your token is valid, and try
                  again. You can also broaden hypothesis fields and refresh.
                </>
              )}
            </p>
          )}

          {!loading && (
            <ol className="overflow-hidden rounded-md border border-rule bg-paper-raised">
            {displayList.map((p, i) => {
              const checked = !!useProtocol[p.id];
              const isExpanded = expandedId === p.id;
              return (
                <li
                  key={p.id}
                  className={cn(
                    "group/paper relative transition-colors",
                    i > 0 ? "border-t border-rule " : "",
                    checked ? "bg-rule-soft/25" : "hover:bg-rule-soft/20",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "absolute inset-y-0 left-0 w-[2px] origin-top scale-y-0 transition-transform duration-500",
                      "bg-sage",
                      checked ? "scale-y-100" : "group-hover/paper:scale-y-100",
                    )}
                  />

                  <div className="px-7 py-5 sm:grid sm:grid-cols-[1fr_auto] sm:items-start sm:gap-6">
                    <div className="min-w-0">
                      <div className="flex min-w-0 gap-2.5 sm:gap-3">
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedId((cur) => (cur === p.id ? null : p.id))
                          }
                          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-rule-soft/50 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-paper-raised"
                          aria-expanded={isExpanded}
                          aria-controls={
                            p.keySteps.length > 0
                              ? `protocol-body-${p.id}`
                              : undefined
                          }
                          id={`protocol-expand-${p.id}`}
                          title={
                            isExpanded ? "Collapse protocol details" : "Expand protocol details"
                          }
                          aria-label={
                            isExpanded ? "Collapse protocol details" : "Expand protocol details"
                          }
                        >
                          <ChevronDown
                            aria-hidden
                            className={cn(
                              "h-4 w-4 transition-transform duration-200",
                              isExpanded && "rotate-180",
                            )}
                          />
                        </button>
                        <div className="min-w-0 flex-1 select-text">
                          <div className="flex items-baseline gap-3 pr-1">
                            <span className="shrink-0 font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <h3
                              id={`protocol-title-${p.id}`}
                              className="font-serif-display text-[20px] leading-[1.3] text-ink sm:text-[22px]"
                            >
                              {p.title}
                            </h3>
                          </div>
                          <p className="mt-2 pl-[2.1rem] font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-primary/80">
                            {p.source}
                          </p>
                          <p
                            className={cn(
                              "mt-3 pl-[2.1rem] text-[15px] leading-[1.75] text-ink-soft/95",
                              !isExpanded && "line-clamp-2",
                            )}
                            style={{ letterSpacing: "0.005em" }}
                          >
                            {p.summary}
                          </p>
                          {p.citation ? (
                            <p className="mt-3 pl-[2.1rem] text-[12px] leading-[1.55] text-muted-foreground">
                              <span className="font-mono-notebook text-[10px] uppercase tracking-[0.16em] text-muted-foreground/90">
                                Source{" "}
                              </span>
                              {p.citation}
                            </p>
                          ) : null}
                        </div>
                      </div>

                      {isExpanded && p.keySteps.length > 0 ? (
                        <div
                          id={`protocol-body-${p.id}`}
                          role="region"
                          aria-labelledby={`protocol-title-${p.id}`}
                          className="mt-4 border-t border-rule/60 pt-4 pl-[2.1rem] ml-6 sm:ml-7"
                        >
                          <ul className="space-y-2">
                            {p.keySteps.map((s, j) => (
                              <li
                                key={j}
                                className="flex gap-2.5 text-[15px] leading-[1.55] text-ink-soft"
                              >
                                <span
                                  aria-hidden
                                  className="mt-2 h-1 w-1 shrink-0 rounded-full bg-sage"
                                />
                                <span>{s}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>

                    <div className="mt-5 flex items-center justify-end border-t border-rule/80 pt-4 sm:mt-0 sm:flex-col sm:items-end sm:justify-start sm:self-stretch sm:border-t-0 sm:pt-0 sm:pl-4 sm:text-right">
                      <div className="flex items-center gap-3">
                        <Label
                          htmlFor={`use-${p.id}`}
                          className="font-mono-notebook text-[11px] uppercase tracking-[0.2em] text-ink-soft cursor-pointer"
                        >
                          Use this protocol
                        </Label>
                        <Checkbox
                          id={`use-${p.id}`}
                          checked={checked}
                          onCheckedChange={(v) =>
                            setUseProtocol((prev) => ({
                              ...prev,
                              [p.id]: v === true,
                            }))
                          }
                          className="border-primary"
                        />
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
            </ol>
          )}
        </section>

        {/* Section 2 — Preferences */}
        <section aria-labelledby="prefs-title" className="mb-12">
          <h2
            id="prefs-title"
            className="mb-4 font-serif-display text-[26px] leading-tight text-ink"
          >
            Preferences / constraints
          </h2>

          <div className="space-y-8 rounded-md border border-rule bg-paper-raised px-7 py-7 sm:px-9">
            <div>
              <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
                Optimize for
              </p>
              <ul className="mt-4 divide-y divide-rule">
                <li className="flex items-start justify-between gap-4 py-4 first:pt-0">
                  <div className="min-w-0 pr-2">
                    <span className="text-[15px] text-ink">Low cost</span>
                    <p className="mt-1.5 text-[13px] leading-[1.5] text-muted-foreground">
                      {PREF_TOGGLE_SUB.lowCost}
                    </p>
                  </div>
                  <Switch
                    className="mt-0.5 shrink-0"
                    checked={prefs.lowCost}
                    onCheckedChange={(v) =>
                      setPrefs((o) => ({ ...o, lowCost: v === true }))
                    }
                    aria-label="Optimize for low cost"
                  />
                </li>
                <li className="flex items-start justify-between gap-4 py-4">
                  <div className="min-w-0 pr-2">
                    <span className="text-[15px] text-ink">High accuracy</span>
                    <p className="mt-1.5 text-[13px] leading-[1.5] text-muted-foreground">
                      {PREF_TOGGLE_SUB.highAccuracy}
                    </p>
                  </div>
                  <Switch
                    className="mt-0.5 shrink-0"
                    checked={prefs.highAccuracy}
                    onCheckedChange={(v) =>
                      setPrefs((o) => ({ ...o, highAccuracy: v === true }))
                    }
                    aria-label="Optimize for high accuracy"
                  />
                </li>
                <li className="flex items-start justify-between gap-4 py-4 last:pb-0">
                  <div className="min-w-0 pr-2">
                    <span className="text-[15px] text-ink">Fast execution</span>
                    <p className="mt-1.5 text-[13px] leading-[1.5] text-muted-foreground">
                      {PREF_TOGGLE_SUB.fastExecution}
                    </p>
                  </div>
                  <Switch
                    className="mt-0.5 shrink-0"
                    checked={prefs.fastExecution}
                    onCheckedChange={(v) =>
                      setPrefs((o) => ({ ...o, fastExecution: v === true }))
                    }
                    aria-label="Optimize for fast execution"
                  />
                </li>
              </ul>
            </div>

            <div>
              <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
                Constraints
              </p>
              <Textarea
                value={prefs.constraints}
                onChange={(e) =>
                  setPrefs((o) => ({ ...o, constraints: e.target.value }))
                }
                placeholder="Any constraints or preferences (equipment, time, conditions)…"
                className="ruled-paper mt-3 min-h-[120px] resize-y border border-rule bg-paper-raised/80 text-[16px] leading-[1.65] text-ink placeholder:text-muted-foreground/70"
              />
            </div>
          </div>
        </section>

        {/* Section 3 — Summary */}
        <section aria-labelledby="summary-title" className="mb-12">
          <h2
            id="summary-title"
            className="mb-4 font-serif-display text-[26px] leading-tight text-ink"
          >
            Summary preview
          </h2>
          <div className="rounded-md border border-rule bg-paper-raised px-7 py-6 sm:px-8">
            <div className="grid gap-6 sm:grid-cols-2">
              <div>
                <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                  Selected protocols
                </p>
                <p className="mt-2 font-serif-display text-[28px] leading-none text-ink">
                  {selectedList.length}
                </p>
                <p className="mt-1.5 text-[14px] text-ink-soft">
                  {selectedList.length === 0
                    ? "None — choose at least one to ground the plan."
                    : "Will inform protocol emphasis and material choices."}
                </p>
              </div>
              <div>
                <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                  Active preferences
                </p>
                {activePreferenceSummaryLines.length === 0 && !prefs.constraints.trim() ? (
                  <p className="mt-2 text-[15px] leading-snug text-ink-soft">
                    No optimization toggles on — use the switches above or add constraints to steer
                    the plan.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-1.5 text-[15px] leading-snug text-ink-soft">
                    {activePreferenceSummaryLines.map((l) => (
                      <li key={l} className="flex gap-2">
                        <span aria-hidden className="shrink-0 text-primary/70">
                          ·
                        </span>
                        <span>{l}</span>
                      </li>
                    ))}
                    {prefs.constraints.trim() ? (
                      <li className="pt-1 text-[14px] italic text-ink-soft/95">
                        “{prefs.constraints.trim().slice(0, 200)}
                        {prefs.constraints.trim().length > 200 ? "…" : ""}”
                      </li>
                    ) : null}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section
          aria-label="Continue to experiment plan"
          className="relative overflow-hidden rounded-md border border-primary/40 bg-paper-raised shadow-[0_1px_0_hsl(var(--primary)/0.15),0_24px_60px_-30px_hsl(var(--primary)/0.35)]"
        >
          <div
            aria-hidden
            className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-rule to-transparent"
          />
          <div className="flex flex-col-reverse items-stretch justify-between gap-4 border-t-0 bg-paper/60 px-7 py-5 sm:flex-row sm:items-center sm:px-9">
            <div className="flex items-center gap-5">
              <button
                type="button"
                onClick={goBack}
                className="group inline-flex items-center gap-2 font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-muted-foreground transition-colors hover:text-ink"
                aria-label="Back to literature check"
              >
                <ArrowRight
                  className="h-4 w-4 rotate-180 transition-transform group-hover:-translate-x-0.5"
                  strokeWidth={1.75}
                />
                Back to literature
              </button>
              <span aria-hidden className="hidden h-4 w-px bg-rule sm:block" />
              <p className="hidden font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-muted-foreground sm:block">
                Ground the planner <span className="text-primary">●</span>
              </p>
            </div>
            <Button
              onClick={() =>
                navigate("/plan", {
                  state: {
                    plan_id: nav?.plan_id,
                    structured: nav?.structured,
                    domain: nav?.domain,
                    protocolSourceSelection: {
                      selected: selectedList,
                      preferences: prefs,
                    } satisfies ProtocolSourceSelection,
                  },
                })
              }
              className="group h-14 gap-3 rounded-sm bg-ink px-7 text-[15px] font-medium text-paper shadow-[0_8px_24px_-12px_hsl(var(--ink)/0.6)] transition-all hover:bg-ink/90 hover:shadow-[0_10px_28px_-10px_hsl(var(--ink)/0.7)]"
            >
              <span className="font-mono-notebook text-[10px] uppercase tracking-[0.24em] opacity-70">
                Step 04 →
              </span>
              <span className="font-serif-display text-[19px] italic">
                Generate experiment plan
              </span>
              <ArrowRight
                className="h-5 w-5 transition-transform group-hover:translate-x-0.5"
                strokeWidth={1.75}
              />
            </Button>
          </div>
        </section>
      </main>
    </div>
  );
};

export default ProtocolSources;
