/**
 * Persist hypothesis + plan id so in-app navigation (workflow menu) can jump
 * between steps without losing router state after a refresh on a single page.
 */

import type { StructuredHypothesis } from "@/lib/api";
import { getWorkflowPlanId } from "@/lib/api";

const STRUCTURED_KEY = "praxis:workflow_structured";

export const WORKFLOW_STEPS = [
  { path: "/lab", short: "Hypothesis", step: "01" },
  { path: "/literature", short: "Literature", step: "02" },
  { path: "/candidates", short: "Protocols", step: "03" },
  { path: "/plan", short: "Plan", step: "04" },
  { path: "/review", short: "Review", step: "05" },
] as const;

export function deriveResearchQuestion(
  s: Pick<
    StructuredHypothesis,
    "independent" | "dependent" | "subject" | "conditions"
  >,
): string {
  return (
    `Does ${s.independent || "the intervention"} affect ` +
    `${s.dependent || "the outcome"} in ` +
    `${s.subject || "the system"}` +
    (s.conditions ? ` under ${s.conditions}` : "") +
    "?"
  );
}

/** Store full structured hypothesis (include research_question when known). */
export function setStoredWorkflowStructured(
  structured: StructuredHypothesis | null,
): void {
  try {
    if (structured) {
      sessionStorage.setItem(STRUCTURED_KEY, JSON.stringify(structured));
    } else {
      sessionStorage.removeItem(STRUCTURED_KEY);
    }
  } catch {
    // private mode, etc.
  }
}

export function getStoredWorkflowStructured(): StructuredHypothesis | null {
  try {
    const raw = sessionStorage.getItem(STRUCTURED_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as StructuredHypothesis;
  } catch {
    return null;
  }
}

export type WorkflowNavTarget = {
  path: string;
  state?: {
    plan_id?: string;
    structured?: StructuredHypothesis;
    domain?: string;
  };
};

/** Build router state for a workflow path using session + active plan id. */
export function getNavTargetForPath(path: string): WorkflowNavTarget {
  const planId = getWorkflowPlanId();
  const structured = getStoredWorkflowStructured();

  if (path === "/lab") {
    return { path: "/lab" };
  }
  if (path === "/literature") {
    if (structured) {
      return { path: "/literature", state: { structured } };
    }
    return { path: "/literature" };
  }
  if (path === "/candidates") {
    if (planId) {
      return { path: "/candidates", state: { plan_id: planId } };
    }
    if (structured) {
      return { path: "/candidates", state: { structured } };
    }
    return { path: "/candidates" };
  }
  if (path === "/plan") {
    if (planId) {
      return { path: "/plan", state: { plan_id: planId } };
    }
    if (structured) {
      return { path: "/plan", state: { structured } };
    }
    return { path: "/plan" };
  }
  if (path === "/review") {
    return { path: "/review" };
  }
  return { path };
}
