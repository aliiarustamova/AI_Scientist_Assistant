/**
 * Builds the Review & Refine overview from the same API views as the plan page.
 */

import type {
  FEProtocolStep,
  FEProtocolView,
  FEMaterialsView,
  StructuredHypothesis,
  TimelineOutput,
} from "@/lib/api";
import { composeHypothesisQuestion } from "@/lib/hypothesis";

export type ReviewOverviewSection = {
  key: "protocol" | "materials" | "budget" | "timeline";
  label: string;
  meta: string;
  preview: string[];
  details: string[];
};

/** Matches the `realBudget` useMemo shape in ExperimentPlan.tsx */
export type RealBudgetForReview = {
  rows: Array<{ label: string; amount: number; priced: number; total: number }>;
  total: number;
  incomplete: boolean;
  pricedCount: number;
  totalCount: number;
} | null;

export type ReviewRefineSnapshot = {
  hypothesisText: string;
  sections: ReviewOverviewSection[];
};

function humanizeDuration(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const s = iso.trim();
  const combined = /^P(\d+)D(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(s);
  if (combined) {
    const [, d, h, m] = combined;
    const parts = [`${d} d`];
    if (h) parts.push(`${h} h`);
    if (m) parts.push(`${m} min`);
    return parts.join(" ");
  }
  const time = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$/.exec(s);
  if (time) {
    const [, h, m, sec] = time;
    const parts: string[] = [];
    if (h) parts.push(`${h} h`);
    if (m) parts.push(`${m} min`);
    if (sec && !h && !m) parts.push(`${sec} s`);
    return parts.length ? parts.join(" ") : null;
  }
  const dOnly = /^P(\d+)D$/.exec(s);
  if (dOnly) return `${dOnly[1]} d`;
  const wOnly = /^P(\d+)W$/.exec(s);
  if (wOnly) return `${wOnly[1]} wk`;
  return s;
}

function collectSteps(view: FEProtocolView | null): FEProtocolStep[] {
  if (!view) return [];
  if (view.procedures && view.procedures.length > 0) {
    return view.procedures.flatMap((p) => p.steps);
  }
  return view.steps ?? [];
}

function formatStepLine(step: FEProtocolStep, index1: number): string {
  const n = String(index1).padStart(2, "0");
  const title =
    step.title?.trim() ||
    step.detail?.split("\n")[0]?.trim().slice(0, 160) ||
    "Step";
  return `${n} — ${title}`;
}

export function buildReviewSnapshot(input: {
  structured: StructuredHypothesis | null | undefined;
  protocolView: FEProtocolView | null;
  materialsView: FEMaterialsView | null;
  timeline: TimelineOutput | null;
  realBudget: RealBudgetForReview;
}): ReviewRefineSnapshot {
  const { structured, protocolView, materialsView, timeline, realBudget } = input;

  const hypothesisText =
    structured?.research_question?.trim() ||
    (structured ? composeHypothesisQuestion(structured) : "") ||
    "Add a hypothesis on the Lab step to see it here.";

  const steps = collectSteps(protocolView);
  const totalDur = humanizeDuration(protocolView?.total_duration ?? null);
  const protoMeta =
    steps.length > 0
      ? `${steps.length} steps` + (totalDur ? ` · ${totalDur} total` : "")
      : "No protocol loaded";

  let stepLines = steps.map((s, i) => formatStepLine(s, i + 1));
  if (stepLines.length === 0) {
    stepLines = ["— No steps returned yet from the plan builder."];
  }

  const matLines: string[] = [];
  let nGroups = 0;
  if (materialsView?.groups?.length) {
    nGroups = materialsView.groups.length;
    for (const g of materialsView.groups) {
      for (const it of g.items) {
        const parts = [it.name.trim()];
        if (it.supplier?.trim()) parts.push(it.supplier.trim());
        if (it.catalog?.trim()) parts.push(it.catalog.trim());
        matLines.push(parts.filter(Boolean).join(" — "));
      }
    }
  }
  if (matLines.length === 0) {
    matLines.push("— No materials list in the last plan response.");
  }

  let budgetMeta = "Estimate unavailable";
  const budgetLines: string[] = [];
  if (realBudget && realBudget.rows.length > 0) {
    budgetMeta = `~$${realBudget.total.toLocaleString("en-US")} USD${
      realBudget.incomplete ? " (lower bound)" : ""
    }`;
    for (const r of realBudget.rows) {
      budgetLines.push(
        `${r.label} — $${r.amount.toLocaleString("en-US")} (${r.priced}/${r.total} line items priced)`,
      );
    }
  } else {
    budgetLines.push(
      "No USD line items parsed from material prices (or still loading).",
    );
  }

  const phases = timeline?.phases ?? [];
  const tMeta = timeline?.total_duration
    ? humanizeDuration(timeline.total_duration) ?? "Timeline"
    : timeline?.partial_total_duration
      ? `≥ ${humanizeDuration(timeline.partial_total_duration) ?? "…"} (partial)`
      : phases.length > 0
        ? `${phases.length} phase(s)`
        : "No timeline";
  const phaseLines: string[] = [];
  for (const p of phases) {
    const name = p.name?.trim() || "Phase";
    const dur = humanizeDuration(p.duration ?? p.partial_duration ?? null);
    phaseLines.push(dur ? `${name} — ${dur}` : name);
  }
  if (phaseLines.length === 0) {
    phaseLines.push("— No schedule phases in the last timeline response.");
  }

  return {
    hypothesisText,
    sections: [
      {
        key: "protocol",
        label: "Protocol",
        meta: protoMeta,
        preview: stepLines.slice(0, 3),
        details: stepLines.slice(3),
      },
      {
        key: "materials",
        label: "Materials",
        meta:
          matLines.length > 0
            ? `${matLines.length} items · ${nGroups} group(s)`
            : "Materials",
        preview: matLines.slice(0, 3),
        details: matLines.slice(3),
      },
      {
        key: "budget",
        label: "Budget",
        meta: budgetMeta,
        preview: budgetLines.slice(0, 3),
        details: budgetLines.slice(3),
      },
      {
        key: "timeline",
        label: "Timeline",
        meta: tMeta,
        preview: phaseLines.slice(0, 3),
        details: phaseLines.slice(3),
      },
    ],
  };
}

export const REVIEW_SNAPSHOT_KEY = "praxis:review_snapshot";
