import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowRight, FlaskConical, Pencil, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { postParseHypothesis } from "@/lib/api";

type StructuredHypothesis = {
  /** Set by API parse; used on the literature page as the main recap line. */
  research_question?: string;
  subject: string;
  independent: string;
  dependent: string;
  conditions: string;
  expected: string;
};

const EMPTY: StructuredHypothesis = {
  research_question: "",
  subject: "",
  independent: "",
  dependent: "",
  conditions: "",
  expected: "",
};

const PLACEHOLDERS: StructuredHypothesis = {
  research_question: "e.g. Does glucose level affect growth rate in E. coli under M9 at 37 °C?",
  subject: "e.g. E. coli K-12 MG1655",
  independent: "e.g. Glucose concentration (0–25 mM)",
  dependent: "e.g. Specific growth rate µ (h⁻¹)",
  conditions: "e.g. 37 °C, aerobic, M9 minimal media",
  expected: "e.g. Inverse, saturating relationship above 10 mM",
};

// Short invite — the old long E. coli example read like prefilled text; keep the field empty-looking.
const PLACEHOLDER_PROMPT =
  "Write your hypothesis here in plain language — a rough sentence or two is enough. You’ll refine the structure in the next panel.";

const FIELD_LABELS: Array<{ key: keyof StructuredHypothesis; label: string; hint: string }> = [
  { key: "subject", label: "Subject", hint: "Organism, system, or material under study" },
  { key: "independent", label: "Independent variable", hint: "What you manipulate" },
  { key: "dependent", label: "Dependent variable", hint: "What you measure" },
  { key: "conditions", label: "Conditions", hint: "Environment held constant" },
  { key: "expected", label: "Expected outcome", hint: "Predicted direction or magnitude" },
];

/**
 * Frontend-only parser (no LLM call).
 * Extracts structured fields from common scientific hypothesis phrasing.
 */
function mockParse(text: string): StructuredHypothesis {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return { ...EMPTY };

  const lower = t.toLowerCase();
  const clean = (s: string) => s.trim().replace(/[.,;:]+$/, "");
  const sentence = t.split(/[.!?]/).map((s) => s.trim()).find(Boolean) ?? t;

  const firstMatch = (patterns: RegExp[]): string => {
    for (const re of patterns) {
      const m = t.match(re);
      if (m?.[1]) return clean(m[1]);
    }
    return "";
  };

  // What is being manipulated?
  const independent = firstMatch([
    /(?:increasing|decreasing|varying|changing)\s+(.+?)(?:\s+(?:will|may|can|is|are|reduces?|increases?|affects?|changes?)\b)/i,
    /(?:effect|impact|influence)\s+of\s+(.+?)(?:\s+on\b)/i,
  ]);

  // What is being measured?
  const dependent = firstMatch([
    /(?:reduces?|decreases?|increases?|affects?|changes?|improves?|impairs?)\s+(?:the\s+)?(.+?)(?:\s+(?:in|of|above|below|under|when|due to)\b|$)/i,
    /(?:effect|impact|influence)\s+of\s+.+?\s+on\s+(.+?)(?:\s+(?:in|of|under|when)\b|$)/i,
  ]);

  // Which organism/system/material?
  const subject = firstMatch([
    /(?:in|of|on)\s+([A-Za-z0-9.\-()\/ ]+?)(?:\s+(?:under|at|with|during|when|using|will|may|can|reduces?|increases?|affects?)\b|$)/i,
    /(?:in|using)\s+(?:the\s+)?([A-Za-z0-9.\-()\/ ]+?)(?:\s+(?:model|system|cells?|strain|culture)\b)/i,
  ]);

  // Experimental context / constraints.
  const conditionMatches = Array.from(
    t.matchAll(
      /\b(?:under|at|during|while|with|without|in)\s+([^.;,]+?(?:conditions?|media|environment|temperature|ph|rpm|oxygen|aerobic|anaerobic|°\s?c|celsius|incubator|culture|time|hours?|minutes?|days?)[^.;,]*)/gi
    )
  ).map((m) => clean(m[1]));
  const conditions = conditionMatches.join("; ");

  // Expected direction/result.
  const expected = firstMatch([
    /(?:we\s+)?(?:expect|predict|hypothesi[sz]e)\s+(?:that\s+)?(.+?)(?:$|[.;])/i,
    /(?:will|should|may|is expected to)\s+(.+?)(?:$|[.;])/i,
  ]);

  // Fallback directional summary if no explicit expectation clause exists.
  const directionalFallback =
    expected ||
    (/\b(increase|improve|enhance|raise)\b/i.test(lower)
      ? "Positive effect is expected."
      : /\b(decrease|reduce|impair|lower)\b/i.test(lower)
        ? "Negative effect is expected."
        : "");

  return {
    research_question: "",
    subject: subject || clean(sentence),
    independent,
    dependent,
    conditions,
    expected: directionalFallback,
  };
}

function todayLabel() {
  return new Date().toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

const HypothesisInput = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [prose, setProse] = useState("");
  const [structured, setStructured] = useState<StructuredHypothesis>(EMPTY);
  const [parsed, setParsed] = useState(false);
  const [isParsing, setIsParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);

  // e.g. return from literature step with { structured } in route state
  useEffect(() => {
    const raw = (location.state as { structured?: Partial<StructuredHypothesis> } | null)
      ?.structured;
    if (!raw) return;
    setStructured({
      research_question: raw.research_question ?? "",
      subject: raw.subject ?? "",
      independent: raw.independent ?? "",
      dependent: raw.dependent ?? "",
      conditions: raw.conditions ?? "",
      expected: raw.expected ?? "",
    });
    setParsed(true);
  }, [location.key]);

  const wordCount = useMemo(
    () => (prose.trim() ? prose.trim().split(/\s+/).length : 0),
    [prose],
  );

  const hasStructure = useMemo(
    () => Object.values(structured).some((v) => v.trim().length > 0),
    [structured],
  );

  const handleParse = async () => {
    const text = prose.trim();
    if (!text) return;
    setIsParsing(true);
    setParseError(null);
    try {
      const res = await postParseHypothesis({ text });
      setStructured({
        research_question: res.structured.research_question ?? "",
        subject: res.structured.subject,
        independent: res.structured.independent,
        dependent: res.structured.dependent,
        conditions: res.structured.conditions,
        expected: res.structured.expected,
      });
      setParsed(true);
    } catch (err) {
      // Fallback keeps the UI usable if backend/LLM is down.
      setStructured(mockParse(text));
      setParsed(true);
      setParseError(
        err instanceof Error
          ? `Using fallback parser: ${err.message}`
          : "Using fallback parser due to a parsing error.",
      );
    } finally {
      setIsParsing(false);
    }
  };

  const updateField = (key: keyof StructuredHypothesis, value: string) =>
    setStructured((s) => ({ ...s, [key]: value }));

  return (
    <div className="relative min-h-screen overflow-hidden bg-paper text-ink">
      {/* Whisper-quiet graph-paper background. ~4% opacity. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 lab-grid" />

      {/* Tiny decorative chemical structure, top-right. ~5% opacity. */}
      <svg
        aria-hidden
        viewBox="0 0 200 200"
        className="pointer-events-none absolute right-6 top-20 hidden h-40 w-40 text-ink opacity-[0.05] sm:right-10 sm:top-24 sm:block"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      >
        <polygon points="100,30 152,60 152,120 100,150 48,120 48,60" />
        <polygon points="100,50 138,72 138,116 100,138 62,116 62,72" />
        <line x1="100" y1="30" x2="100" y2="10" />
        <circle cx="100" cy="6" r="4" />
        <line x1="152" y1="60" x2="172" y2="48" />
        <circle cx="176" cy="46" r="4" />
        <line x1="48" y1="120" x2="28" y2="132" />
        <circle cx="24" cy="134" r="4" />
        <line x1="100" y1="150" x2="100" y2="172" />
        <circle cx="100" cy="176" r="4" />
        <line x1="62" y1="72" x2="48" y2="60" />
        <line x1="138" y1="116" x2="152" y2="120" />
      </svg>

      {/* Header */}
      <header className="relative border-b border-rule">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 sm:px-10">
          <a href="/" className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-7 w-7 items-center justify-center rounded-sm border border-rule bg-paper-raised"
            >
              <FlaskConical className="h-4 w-4 text-primary" strokeWidth={1.5} />
            </span>
            <span className="font-serif-display text-xl tracking-tight text-ink">
              Praxis
            </span>
          </a>
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

      {/* Main */}
      <main className="relative mx-auto max-w-6xl px-6 pb-20 pt-12 sm:px-10 sm:pt-16">
        <section aria-labelledby="page-title" className="mb-12">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="font-mono-notebook text-[13px] uppercase tracking-[0.22em] text-muted-foreground">
                <span className="text-primary">●</span>&nbsp;&nbsp;New entry · Step <span className="text-ink">01</span> of 05
              </p>
              <h1
                id="page-title"
                className="mt-5 font-serif-display text-[52px] leading-[1.02] text-ink sm:text-[68px]"
              >
                Draft a{" "}
                <span className="italic text-primary">hypothesis</span>
                <span className="text-primary">.</span>
              </h1>
              <p className="mt-6 max-w-xl text-[17px] leading-[1.75] text-ink-soft">
                Capture your hypothesis in your own words — even if it's rough or
                incomplete. We'll structure it into a formal experimental definition so
                the next steps —{" "}
                <span className="font-serif-display italic text-ink">literature review</span>,{" "}
                <span className="font-serif-display italic text-ink">protocol design</span>, and{" "}
                <span className="font-serif-display italic text-ink">budgeting</span> — stay
                aligned with your original intent.
              </p>
            </div>
            <p className="font-mono-notebook text-[13px] tracking-[0.04em] text-muted-foreground sm:text-right">
              <span className="text-ink-soft">Entry</span> · {todayLabel()}
            </p>
          </div>
          <div className="mt-10 h-px w-full bg-rule" />
        </section>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          {/* Free-text panel */}
          <section
            aria-label="Hypothesis prose"
            className="flex flex-col rounded-md border border-rule bg-paper-raised"
          >
            <header className="flex items-center justify-between border-b border-rule px-7 py-5">
              <h2 className="font-serif-display text-[26px] leading-tight text-ink">Describe your hypothesis</h2>
              <span className="font-mono-notebook text-[13px] uppercase tracking-[0.2em] text-muted-foreground">
                {wordCount} {wordCount === 1 ? "word" : "words"}
              </span>
            </header>

            <div className="flex flex-1 flex-col px-7 pb-7 pt-6">
              <Textarea
                value={prose}
                onChange={(e) => setProse(e.target.value)}
                placeholder={PLACEHOLDER_PROMPT}
                aria-label="Hypothesis prose"
                className="ruled-paper min-h-[400px] flex-1 resize-none border-0 bg-transparent px-0 py-0 text-[22px] leading-[1.85rem] text-ink shadow-none placeholder:text-muted-foreground/70 focus-visible:ring-0 focus-visible:ring-offset-0"
                style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
              />
            </div>

            <footer className="flex flex-col-reverse items-start justify-between gap-3 border-t border-rule px-7 py-5 sm:flex-row sm:items-center">
              <Button
                onClick={handleParse}
                disabled={!prose.trim() || isParsing}
                className="h-11 gap-2 rounded-sm bg-primary px-6 text-[14px] font-medium text-primary-foreground hover:bg-primary/90"
              >
                <Sparkles className="h-4 w-4" strokeWidth={2} />
                {isParsing
                  ? "Parsing hypothesis..."
                  : parsed
                    ? "Re-parse hypothesis"
                    : "Parse hypothesis"}
              </Button>
            </footer>
            {parseError && (
              <p className="px-7 pb-4 text-[12px] leading-[1.5] text-[hsl(var(--destructive))]">
                {parseError}
              </p>
            )}
          </section>

          {/* Structured panel — active control surface */}
          <section
            aria-label="Parsed structure"
            className="flex flex-col rounded-md border border-rule bg-paper-raised"
          >
            <header className="border-b border-rule px-7 py-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="font-serif-display text-[26px] leading-tight text-ink">
                    Parsed structure
                  </h2>
                  <p
                    className="mt-1.5 text-[15px] italic leading-snug text-ink-soft"
                    style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
                  >
                    Review &amp; refine before analysis.
                  </p>
                </div>
                <span
                  className={
                    "inline-flex items-center gap-1.5 rounded-sm border px-2.5 py-1 font-mono-notebook text-[11px] uppercase tracking-[0.2em] transition-colors " +
                    (parsed
                      ? "border-primary/40 bg-primary/[0.06] text-primary"
                      : "border-rule text-muted-foreground")
                  }
                >
                  <span
                    aria-hidden
                    className={
                      "h-1.5 w-1.5 rounded-full " +
                      (parsed ? "bg-primary animate-pulse" : "bg-muted-foreground/50")
                    }
                  />
                  {parsed ? "Editable" : "Awaiting input"}
                </span>
              </div>
            </header>

            <div className="divide-y divide-rule">
              {FIELD_LABELS.map(({ key, label, hint }) => {
                const filled = structured[key].trim().length > 0;
                return (
                  <div
                    key={key}
                    className="group/field relative grid grid-cols-1 gap-2.5 px-7 py-5 transition-colors hover:bg-rule-soft/30 focus-within:bg-rule-soft/40"
                  >
                    {/* active accent rail (left) */}
                    <span
                      aria-hidden
                      className="pointer-events-none absolute inset-y-3 left-0 w-[2px] rounded-r-sm bg-primary opacity-0 transition-opacity group-hover/field:opacity-40 group-focus-within/field:opacity-100"
                    />
                    <div className="flex items-baseline justify-between gap-3">
                      <label
                        htmlFor={`field-${key}`}
                        className="flex items-center gap-2 font-mono-notebook text-[13px] uppercase tracking-[0.2em] text-ink-soft"
                      >
                        {label}
                        <span
                          aria-hidden
                          className={
                            "h-1.5 w-1.5 rounded-full transition-colors " +
                            (filled ? "bg-primary/70" : "bg-rule")
                          }
                        />
                      </label>
                      <span
                        className="hidden text-[14px] italic text-muted-foreground sm:inline"
                        style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
                      >
                        {hint}
                      </span>
                    </div>
                    <div className="relative">
                      <Input
                        id={`field-${key}`}
                        value={structured[key]}
                        onChange={(e) => updateField(key, e.target.value)}
                        placeholder={PLACEHOLDERS[key]}
                        className="h-auto rounded-none border-0 border-b border-rule/60 bg-transparent px-0 py-3 pr-8 text-[26px] leading-[1.4] text-ink shadow-none transition-colors placeholder:text-[22px] placeholder:text-muted-foreground/60 hover:border-ink/40 focus-visible:border-primary focus-visible:ring-0 focus-visible:ring-offset-0"
                        style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
                      />
                      <Pencil
                        aria-hidden
                        className="pointer-events-none absolute right-0 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/60 opacity-0 transition-opacity group-hover/field:opacity-100 group-focus-within/field:opacity-0"
                        strokeWidth={1.5}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            <footer className="flex min-h-[5.25rem] items-center justify-between border-t border-rule px-7 py-5">
              <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                Click any field to edit
              </p>
              <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
                {Object.values(structured).filter((v) => v.trim()).length}/{FIELD_LABELS.length} filled
              </p>
            </footer>
          </section>
        </div>

        {/* Step transition + CTA */}
        <section
          aria-label="Continue to next step"
          className={
            "relative mt-16 overflow-hidden rounded-md border bg-paper-raised transition-colors " +
            (hasStructure
              ? "border-primary/40 shadow-[0_1px_0_hsl(var(--primary)/0.15),0_24px_60px_-30px_hsl(var(--primary)/0.35)]"
              : "border-rule")
          }
        >
          {/* faint progress rail */}
          <div aria-hidden className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-rule to-transparent" />

          <div className="grid grid-cols-1 gap-0 sm:grid-cols-[1fr_auto_1fr]">
            {/* Step 01 — done/in progress */}
            <div className="px-7 py-7 sm:px-9 sm:py-9">
              <div className="flex items-center gap-3">
                <span
                  className={
                    "flex h-7 w-7 items-center justify-center rounded-full border text-[12px] font-medium transition-colors " +
                    (hasStructure
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-rule bg-paper text-muted-foreground")
                  }
                >
                  {hasStructure ? "✓" : "01"}
                </span>
                <p className="font-mono-notebook text-[12px] uppercase tracking-[0.22em] text-muted-foreground">
                  Step 01
                </p>
              </div>
              <h3 className="mt-4 font-serif-display text-[26px] leading-tight text-ink">
                Draft hypothesis
              </h3>
              <p className="mt-2 text-[14px] leading-[1.65] text-ink-soft">
                {hasStructure
                  ? "Structured and ready. You can keep refining the fields above."
                  : "Write your prose, then parse to populate the structure."}
              </p>
            </div>

            {/* Arrow / connector */}
            <div className="flex items-center justify-center border-rule px-6 py-2 sm:border-x sm:px-8 sm:py-9">
              <div className="flex flex-col items-center gap-2 sm:gap-3" aria-hidden>
                <span className="hidden h-8 w-px bg-rule sm:block" />
                <ArrowRight
                  className={
                    "h-5 w-5 transition-colors " +
                    (hasStructure ? "text-primary" : "text-muted-foreground/50")
                  }
                  strokeWidth={1.75}
                />
                <span className="hidden h-8 w-px bg-rule sm:block" />
              </div>
            </div>

            {/* Step 02 — next */}
            <div className="px-7 py-7 sm:px-9 sm:py-9">
              <div className="flex items-center gap-3">
                <span
                  className={
                    "flex h-7 w-7 items-center justify-center rounded-full border text-[12px] font-medium transition-colors " +
                    (hasStructure
                      ? "border-primary/60 bg-paper text-primary"
                      : "border-rule bg-paper text-muted-foreground")
                  }
                >
                  02
                </span>
                <p
                  className={
                    "font-mono-notebook text-[12px] uppercase tracking-[0.22em] transition-colors " +
                    (hasStructure ? "text-primary" : "text-muted-foreground")
                  }
                >
                  Step 02 — Up next
                </p>
              </div>
              <h3 className="mt-4 font-serif-display text-[26px] leading-tight text-ink">
                Literature check
              </h3>
              <p className="mt-2 text-[14px] leading-[1.65] text-ink-soft">
                We search prior work and flag whether your idea looks{" "}
                <span className="italic">novel</span>,{" "}
                <span className="italic">similar</span>, or{" "}
                <span className="italic">already published</span>.
              </p>
            </div>
          </div>

          {/* CTA bar */}
          <div className="flex flex-col-reverse items-stretch justify-between gap-4 border-t border-rule bg-paper/60 px-7 py-5 sm:flex-row sm:items-center sm:px-9">
            <div className="flex items-center gap-5">
              <button
                type="button"
                onClick={() => navigate("/")}
                className="group inline-flex items-center gap-2 font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-muted-foreground transition-colors hover:text-ink"
                aria-label="Go back to welcome"
              >
                <ArrowRight className="h-4 w-4 rotate-180 transition-transform group-hover:-translate-x-0.5" strokeWidth={1.75} />
                Back
              </button>
              <span aria-hidden className="hidden h-4 w-px bg-rule sm:block" />
              <p className="hidden font-mono-notebook text-[12px] uppercase tracking-[0.2em] text-muted-foreground sm:block">
                {hasStructure
                  ? <>Ready when you are <span className="text-primary">●</span></>
                  : "Parse a hypothesis above to unlock"}
              </p>
            </div>
            <Button
              onClick={() => {
                // Pass the structured hypothesis forward via router state
                // so /literature can call POST /lit-review with it.
                // The backend StructuredHypothesis includes a research_question
                // field this form doesn't collect — derive a sensible default
                // ("Does X affect Y in Z under conditions?") rather than
                // adding another input.
                const research_question =
                  structured.research_question?.trim() ||
                  `Does ${structured.independent || "the intervention"} affect ` +
                  `${structured.dependent || "the outcome"} in ` +
                  `${structured.subject || "the system"}` +
                  (structured.conditions ? ` under ${structured.conditions}` : "") +
                  "?";
                navigate("/literature", {
                  state: {
                    structured: { ...structured, research_question },
                    domain: undefined,
                  },
                });
              }}
              disabled={!hasStructure}
              className="group h-14 gap-3 rounded-sm bg-ink px-7 text-[15px] font-medium text-paper shadow-[0_8px_24px_-12px_hsl(var(--ink)/0.6)] transition-all hover:bg-ink/90 hover:shadow-[0_10px_28px_-10px_hsl(var(--ink)/0.7)] disabled:bg-ink/20 disabled:text-paper/70 disabled:shadow-none"
            >
              <span className="font-mono-notebook text-[10px] uppercase tracking-[0.24em] opacity-70">
                Step 02 →
              </span>
              <span className="font-serif-display text-[19px] italic">
                Literature check
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

export default HypothesisInput;
