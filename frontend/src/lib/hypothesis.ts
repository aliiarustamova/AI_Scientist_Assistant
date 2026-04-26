// Shared hypothesis-text helpers. Deterministic — same structured input
// always produces the same prose, no LLM in the loop. Used by:
//   - ExperimentPlan.tsx for the hypothesis recap card (composes a
//     fallback sentence when research_question is blank)
//   - LiteratureCheck.tsx (parallel `deriveHypothesisParts` keeps its
//     own copy because it emits color-coded tokens, not flat text;
//     the wording here mirrors that breakdown).

import type { StructuredHypothesis } from "@/lib/api";

export function composeHypothesisQuestion(s: StructuredHypothesis): string {
  const intervention = s.independent?.trim() || "the intervention";
  const outcome = s.dependent?.trim() || "the outcome";
  const subject = s.subject?.trim() || "the system";
  const conditions = s.conditions?.trim();
  const base = `Does ${intervention} affect ${outcome} in ${subject}`;
  return conditions ? `${base} under ${conditions}?` : `${base}?`;
}
