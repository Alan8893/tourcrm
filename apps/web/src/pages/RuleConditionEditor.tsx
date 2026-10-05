import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { isRuleGroup, type RuleCatalog, type RuleGroup, type RuleLeaf, type RuleNode } from "../api/achievements";
import { newLeaf, newRootCondition } from "../domain/achievementRules";
import { logicLabel } from "../domain/achievementsFormat";
import { SelectField } from "./InventoryForms";
import styles from "./Achievements.module.css";

/**
 * Structured editor for a Rule Version's nested `AND`/`OR` condition tree
 * (Issue #220, A5). It only edits data: metrics, comparison and logic
 * operators come from the backend's `rule-catalog` (A8 — only approved
 * metrics are offered), and the tree is validated by the backend on save.
 * It never evaluates a rule.
 */

type NodeEditorProps<T extends RuleNode> = {
  node: T;
  catalog: RuleCatalog;
  depth: number;
  onChange: (node: T) => void;
  onRemove?: () => void;
};

function LeafEditor({ node, catalog, onChange, onRemove }: NodeEditorProps<RuleLeaf>) {
  return (
    <div className={styles.leaf} data-testid="rule-leaf">
      <SelectField
        label="Показатель"
        value={node.metric}
        options={catalog.metrics.map((metric) => ({ value: metric.code, label: metric.label }))}
        onChange={(metric) => onChange({ ...node, metric })}
      />
      <SelectField
        label="Сравнение"
        value={node.operator}
        options={catalog.comparison_operators.map((operator) => ({ value: operator, label: operator }))}
        onChange={(operator) => onChange({ ...node, operator })}
      />
      <Input
        label="Значение"
        type="number"
        min={0}
        step={1}
        value={Number.isNaN(node.value) ? "" : String(node.value)}
        onChange={(event) =>
          onChange({ ...node, value: event.target.value === "" ? Number.NaN : Number(event.target.value) })
        }
      />
      {onRemove ? (
        <Button type="button" variant="secondary" onClick={onRemove}>
          Удалить условие
        </Button>
      ) : null}
    </div>
  );
}

function GroupEditor({ node, catalog, depth, onChange, onRemove }: NodeEditorProps<RuleGroup>) {
  function replaceChild(index: number, child: RuleNode) {
    onChange({ ...node, conditions: node.conditions.map((item, i) => (i === index ? child : item)) });
  }
  function removeChild(index: number) {
    onChange({ ...node, conditions: node.conditions.filter((_, i) => i !== index) });
  }
  const canNest = depth < catalog.max_depth - 1;

  return (
    <fieldset className={styles.group} data-testid="rule-group">
      <legend className={styles.legend}>{depth === 0 ? "Условия правила" : "Группа условий"}</legend>
      <SelectField
        label="Логика группы"
        value={node.logic}
        options={catalog.logic_operators.map((logic) => ({
          value: logic,
          label: logic === "AND" ? "И — все условия" : logic === "OR" ? "ИЛИ — любое условие" : logicLabel(logic),
        }))}
        onChange={(logic) => onChange({ ...node, logic })}
      />
      {node.conditions.map((child, index) =>
        isRuleGroup(child) ? (
          <GroupEditor
            key={index}
            node={child}
            catalog={catalog}
            depth={depth + 1}
            onChange={(next) => replaceChild(index, next)}
            onRemove={() => removeChild(index)}
          />
        ) : (
          <LeafEditor
            key={index}
            node={child}
            catalog={catalog}
            depth={depth + 1}
            onChange={(next) => replaceChild(index, next)}
            onRemove={() => removeChild(index)}
          />
        ),
      )}
      <div className={styles.actions}>
        <Button
          type="button"
          variant="secondary"
          icon="action.add"
          onClick={() => onChange({ ...node, conditions: [...node.conditions, newLeaf(catalog)] })}
        >
          Условие
        </Button>
        {canNest ? (
          <Button
            type="button"
            variant="secondary"
            icon="action.add"
            onClick={() =>
              onChange({ ...node, conditions: [...node.conditions, newRootCondition(catalog)] })
            }
          >
            Группа
          </Button>
        ) : null}
        {onRemove ? (
          <Button type="button" variant="secondary" onClick={onRemove}>
            Удалить группу
          </Button>
        ) : null}
      </div>
    </fieldset>
  );
}

export function RuleConditionEditor({
  value,
  catalog,
  onChange,
}: {
  value: RuleNode;
  catalog: RuleCatalog;
  onChange: (node: RuleNode) => void;
}) {
  if (isRuleGroup(value)) {
    return <GroupEditor node={value} catalog={catalog} depth={0} onChange={onChange} />;
  }
  return (
    <GroupEditor
      node={{ logic: catalog.logic_operators[0] ?? "AND", conditions: [value] }}
      catalog={catalog}
      depth={0}
      onChange={onChange}
    />
  );
}
