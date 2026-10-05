import type { RuleCatalog, RuleGroup, RuleLeaf } from "../api/achievements";

/** Starting values for the Rule condition editor (Issue #220): built only
 * from what the backend's `rule-catalog` declares — never a hardcoded
 * metric. Data construction only; the backend validates the tree. */

export function newLeaf(catalog: RuleCatalog): RuleLeaf {
  return {
    metric: catalog.metrics[0]?.code ?? "",
    operator: catalog.comparison_operators[0] ?? ">=",
    value: 1,
  };
}

export function newRootCondition(catalog: RuleCatalog): RuleGroup {
  return { logic: catalog.logic_operators[0] ?? "AND", conditions: [newLeaf(catalog)] };
}
