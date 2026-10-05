import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useCreateRuleVersion,
  useDefinition,
  useNormativeSets,
  useNormativeVersions,
  useRuleCatalog,
  useRuleVersions,
  useSetDefinitionStatus,
  useSetRuleVersionStatus,
  useUpdateDefinition,
  type AchievementDefinition,
  type RuleCatalog,
  type RuleNode,
} from "../api/achievements";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import {
  DEFINITION_AWARD_METHOD_LABELS,
  LIFECYCLE_LABELS,
  REPEATABILITY_LABELS,
  SOURCE_LABELS,
  achievementErrorMessage,
  describeRule,
  formatDateTime,
  lifecycleIcon,
} from "../domain/achievementsFormat";
import { newRootCondition } from "../domain/achievementRules";
import { AwardsTab, Meta } from "./AchievementsPage";
import { FormDialog, SelectField, TextAreaField } from "./InventoryForms";
import { RuleConditionEditor } from "./RuleConditionEditor";
import styles from "./Achievements.module.css";

/** Achievement Definition detail (Issue #220): lifecycle (A1), Rule
 * Versions (A4/A5/A11) and the Definition's Award history. Administrator-
 * only route; the backend authorizes every request. */
export function AchievementDefinitionPage() {
  const { definitionId } = useParams();
  const definition = useDefinition(definitionId);
  const catalog = useRuleCatalog();
  const setStatus = useSetDefinitionStatus();
  const notify = useNotify();
  const [editing, setEditing] = useState(false);

  if (definition.isLoading) return <Loading label="Загружаем достижение…" />;
  if (definition.isError || !definition.data) {
    return <ErrorState illustration="404" title="Достижение не найдено" />;
  }
  const data = definition.data;
  const active = data.status === "active";

  return (
    <div className={styles.page}>
      <PageHeader
        title={data.name}
        description={data.description ?? undefined}
        back={{ to: "/achievements", label: "Достижения" }}
        titleExtra={<StatusBadge status={lifecycleIcon(data.status)} label={LIFECYCLE_LABELS[data.status]} />}
        actions={
          <div className={styles.actions}>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Изменить описание
            </Button>
            <Button
              variant={active ? "secondary" : "primary"}
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus.mutate(
                  { definitionId: data.id, active: !active },
                  {
                    onSuccess: (updated) =>
                      notify(
                        "success",
                        updated.status === "active" ? "Достижение активировано" : "Достижение деактивировано",
                      ),
                    onError: (error) => notify("error", achievementErrorMessage(error)),
                  },
                )
              }
            >
              {active ? "Деактивировать" : "Активировать"}
            </Button>
          </div>
        }
      />
      <Card>
        <dl className={styles.meta}>
          <Meta label="Код" value={data.code} />
          <Meta label="Источник" value={SOURCE_LABELS[data.source]} />
          <Meta label="Способ выдачи" value={DEFINITION_AWARD_METHOD_LABELS[data.award_method]} />
          <Meta label="Повторяемость" value={REPEATABILITY_LABELS[data.repeatability]} />
        </dl>
        <p className={styles.muted}>
          Деактивация не отзывает и не изменяет уже выданные достижения.
        </p>
      </Card>

      <RuleVersionsSection definition={data} catalog={catalog.data} />

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>История выдач</h2>
        </div>
        <AwardsTab definitionId={data.id} />
      </section>

      {editing ? <EditDefinitionDialog definition={data} onClose={() => setEditing(false)} /> : null}
    </div>
  );
}

function EditDefinitionDialog({
  definition,
  onClose,
}: {
  definition: AchievementDefinition;
  onClose: () => void;
}) {
  const [name, setName] = useState(definition.name);
  const [description, setDescription] = useState(definition.description ?? "");
  const update = useUpdateDefinition();
  return (
    <FormDialog
      title="Описание достижения"
      submitLabel="Сохранить"
      pending={update.isPending}
      error={update.isError ? achievementErrorMessage(update.error) : null}
      canSubmit={name.trim() !== ""}
      onClose={onClose}
      onSubmit={() =>
        update.mutate(
          { definitionId: definition.id, fields: { name: name.trim(), description: description.trim() || null } },
          { onSuccess: onClose },
        )
      }
    >
      <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} />
      <TextAreaField label="Описание" value={description} onChange={setDescription} />
    </FormDialog>
  );
}

function RuleVersionsSection({
  definition,
  catalog,
}: {
  definition: AchievementDefinition;
  catalog: RuleCatalog | undefined;
}) {
  const versions = useRuleVersions(definition.id);
  const setStatus = useSetRuleVersionStatus();
  const notify = useNotify();
  const [creating, setCreating] = useState(false);

  return (
    <section className={styles.section}>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>Версии правила</h2>
        <Button
          variant="primary"
          icon="action.add"
          disabled={!catalog}
          onClick={() => setCreating(true)}
        >
          Новая версия правила
        </Button>
      </div>
      <p className={styles.muted}>
        Версия правила не изменяется после создания. Чтобы изменить условия или нормативы,
        создайте новую версию — прежние версии сохраняются. Действует только одна активная версия.
      </p>
      {versions.isLoading ? <Loading label="Загружаем версии…" /> : null}
      {versions.isError ? <ErrorState illustration="error" title="Не удалось загрузить версии" /> : null}
      {versions.data && versions.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Версий правила пока нет" />
      ) : null}
      {versions.data && versions.data.items.length > 0 ? (
        <ul className={styles.list}>
          {versions.data.items.map((version) => (
            <li key={version.id}>
              <Card data-testid="rule-version-card">
                <div className={styles.rowHeader}>
                  <h3 className={styles.rowTitle}>Версия {version.version_number}</h3>
                  <StatusBadge
                    status={lifecycleIcon(version.status)}
                    label={LIFECYCLE_LABELS[version.status]}
                  />
                </div>
                <p className={styles.rule}>{describeRule(version.condition, catalog)}</p>
                <dl className={styles.meta}>
                  <Meta label="Создана" value={formatDateTime(version.created_at)} />
                  <Meta label="Использована в выдачах" value={version.is_used ? "Да" : "Нет"} />
                  {version.normative_set_version_id ? (
                    <Meta label="Нормативы" value={version.normative_set_version_id} />
                  ) : null}
                </dl>
                <div className={styles.cardActions}>
                  <Button
                    variant="secondary"
                    disabled={setStatus.isPending}
                    onClick={() =>
                      setStatus.mutate(
                        { ruleVersionId: version.id, active: version.status !== "active" },
                        { onError: (error) => notify("error", achievementErrorMessage(error)) },
                      )
                    }
                  >
                    {version.status === "active" ? "Деактивировать" : "Сделать активной"}
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
      {creating && catalog ? (
        <RuleVersionDialog definition={definition} catalog={catalog} onClose={() => setCreating(false)} />
      ) : null}
    </section>
  );
}

function RuleVersionDialog({
  definition,
  catalog,
  onClose,
}: {
  definition: AchievementDefinition;
  catalog: RuleCatalog;
  onClose: () => void;
}) {
  const [condition, setCondition] = useState<RuleNode>(() => newRootCondition(catalog));
  const [normativeSetId, setNormativeSetId] = useState("");
  const [normativeVersionId, setNormativeVersionId] = useState("");
  const sets = useNormativeSets();
  const normativeVersions = useNormativeVersions(normativeSetId || undefined);
  const create = useCreateRuleVersion();
  const notify = useNotify();

  function submit() {
    create.mutate(
      {
        definitionId: definition.id,
        fields: { condition, normative_set_version_id: normativeVersionId || null },
      },
      {
        onSuccess: (created) => {
          notify("success", `Создана версия правила ${created.version_number}`);
          onClose();
        },
      },
    );
  }

  return (
    <FormDialog
      title="Новая версия правила"
      description="Создаётся новая неактивная версия со своим номером; существующие версии не меняются. Доступны только утверждённые показатели."
      submitLabel="Создать версию"
      pending={create.isPending}
      error={create.isError ? achievementErrorMessage(create.error) : null}
      onClose={onClose}
      onSubmit={submit}
    >
      <RuleConditionEditor value={condition} catalog={catalog} onChange={setCondition} />
      <SelectField
        label="Набор нормативов"
        value={normativeSetId}
        placeholder={definition.source === "fstr" ? "Выберите набор" : "Без нормативов"}
        options={(sets.data?.items ?? []).map((set) => ({ value: set.id, label: set.name }))}
        onChange={(value) => {
          setNormativeSetId(value);
          setNormativeVersionId("");
        }}
      />
      <SelectField
        label="Версия нормативов"
        value={normativeVersionId}
        placeholder={definition.source === "fstr" ? "Выберите версию" : "Без нормативов"}
        options={(normativeVersions.data?.items ?? []).map((item) => ({
          value: item.id,
          label: `v${item.version_number} — ${item.document_version} (${LIFECYCLE_LABELS[item.status]})`,
        }))}
        onChange={setNormativeVersionId}
        hint={
          definition.source === "fstr"
            ? "Для достижения ФСТР правило ссылается на конкретную версию нормативов."
            : undefined
        }
      />
    </FormDialog>
  );
}
