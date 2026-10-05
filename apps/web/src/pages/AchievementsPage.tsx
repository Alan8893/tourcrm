import { useState } from "react";
import { Link } from "react-router-dom";

import { useCurrentUser } from "../api/auth";
import {
  useAllActiveDefinitions,
  useAwards,
  useCreateDefinition,
  useCreateManualAward,
  useCreateNormativeSet,
  useDefinitions,
  useNormativeSets,
  useRevokeAward,
  useRuleVersions,
  useRunReconciliation,
  type AchievementAward,
  type AchievementSource,
  type AwardMethod,
  type AwardStatus,
  type DefinitionAwardMethod,
  type LifecycleStatus,
  type Repeatability,
} from "../api/achievements";
import { personFullName, usePersons } from "../api/people";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { FilterSelect } from "../components/ui/FilterSelect";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Tabs } from "../components/ui/Tabs";
import { useNotify } from "../components/ui/notificationContext";
import {
  AWARD_METHOD_LABELS,
  AWARD_STATUS_LABELS,
  DEFINITION_AWARD_METHOD_LABELS,
  LIFECYCLE_LABELS,
  REPEATABILITY_LABELS,
  SOURCE_LABELS,
  TRIGGER_LABELS,
  achievementErrorMessage,
  awardStatusIcon,
  formatDateTime,
  lifecycleIcon,
} from "../domain/achievementsFormat";
import { hasAdministratorRole } from "../shell/navigation";
import { FormDialog, SelectField, TextAreaField } from "./InventoryForms";
import { PlaceholderPage } from "./PlaceholderPage";
import styles from "./Achievements.module.css";

/**
 * «Достижения» (Issue #220). For the Administrator this is the
 * Achievements administration (docs/04-modules/achievements-and-norms.md
 * §6): Achievement Definitions, Normative Requirement Sets, the Award
 * history with manual awarding and revocation, and an on-demand Engine
 * reconciliation. Every other role keeps the section placeholder — the
 * participant/guardian views are not part of this slice. The admin check
 * here is UX only; the backend enforces `achievement.manage` /
 * `achievement.award` / `achievement.read` on every request.
 */
export function AchievementsPage() {
  const { data } = useCurrentUser();
  if (!hasAdministratorRole(data?.role_assignments ?? [])) {
    return <PlaceholderPage title="Достижения" />;
  }
  return <AchievementsAdministration />;
}

type TabId = "definitions" | "normative" | "awards";

function AchievementsAdministration() {
  const [tab, setTab] = useState<TabId>("definitions");
  const reconcile = useRunReconciliation();
  const notify = useNotify();

  return (
    <div className={styles.page}>
      <PageHeader
        title="Достижения"
        description="Клубные достижения и достижения по нормативам ФСТР: правила, версии нормативов, выдачи."
        actions={
          <Button
            variant="secondary"
            disabled={reconcile.isPending}
            onClick={() =>
              reconcile.mutate(undefined, {
                onSuccess: (result) =>
                  notify(
                    "success",
                    result.awards_created > 0
                      ? `Сверка завершена: выдано недостающих достижений — ${result.awards_created}.`
                      : "Сверка завершена: недостающих достижений нет.",
                  ),
                onError: (error) => notify("error", achievementErrorMessage(error)),
              })
            }
          >
            Запустить сверку
          </Button>
        }
      />
      <Tabs
        label="Разделы достижений"
        activeId={tab}
        onChange={(id) => setTab(id as TabId)}
        items={[
          { id: "definitions", label: "Достижения", content: <DefinitionsTab /> },
          { id: "normative", label: "Нормативы", content: <NormativeSetsTab /> },
          { id: "awards", label: "Выдачи", content: <AwardsTab /> },
        ]}
      />
    </div>
  );
}

// --- Definitions ---------------------------------------------------------------------

const DEFINITION_STATUS_OPTIONS = [
  { value: "", label: "Все" },
  { value: "active", label: "Активные" },
  { value: "inactive", label: "Неактивные" },
];

function DefinitionsTab() {
  const [status, setStatus] = useState<LifecycleStatus | "">("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const query = useDefinitions({ page, status });

  return (
    <div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Состояние"
          value={status}
          options={DEFINITION_STATUS_OPTIONS}
          onChange={(value) => {
            setStatus(value as LifecycleStatus | "");
            setPage(1);
          }}
        />
        <div className={styles.actions}>
          <Button variant="primary" icon="action.add" onClick={() => setCreating(true)}>
            Создать достижение
          </Button>
        </div>
      </div>

      {query.isLoading ? <Loading label="Загружаем достижения…" /> : null}
      {query.isError ? (
        <ErrorState illustration="error" title="Не удалось загрузить достижения" />
      ) : null}
      {query.data && query.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Достижений пока нет" />
      ) : null}
      {query.data && query.data.items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {query.data.items.map((definition) => (
              <li key={definition.id}>
                <Card data-testid="definition-card">
                  <div className={styles.rowHeader}>
                    <h3 className={styles.rowTitle}>
                      <Link
                        className={styles.rowTitleLink}
                        to={`/achievements/definitions/${definition.id}`}
                      >
                        {definition.name}
                      </Link>
                    </h3>
                    <StatusBadge
                      status={lifecycleIcon(definition.status)}
                      label={LIFECYCLE_LABELS[definition.status]}
                    />
                  </div>
                  <dl className={styles.meta}>
                    <Meta label="Код" value={definition.code} />
                    <Meta label="Источник" value={SOURCE_LABELS[definition.source]} />
                    <Meta label="Способ выдачи" value={DEFINITION_AWARD_METHOD_LABELS[definition.award_method]} />
                    <Meta label="Повторяемость" value={REPEATABILITY_LABELS[definition.repeatability]} />
                  </dl>
                </Card>
              </li>
            ))}
          </ul>
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}

      {creating ? <CreateDefinitionDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

export function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.metaEntry}>
      <dt className={styles.metaLabel}>{label}</dt>
      <dd className={styles.metaValue}>{value}</dd>
    </div>
  );
}

function optionsOf<T extends string>(labels: Record<T, string>) {
  return (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
}

function CreateDefinitionDialog({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState<AchievementSource>("club");
  const [awardMethod, setAwardMethod] = useState<DefinitionAwardMethod>("automatic");
  const [repeatability, setRepeatability] = useState<Repeatability>("non_repeatable");
  const create = useCreateDefinition();
  const notify = useNotify();

  return (
    <FormDialog
      title="Новое достижение"
      description="Достижение создаётся неактивным. Источник, способ выдачи и повторяемость после создания не меняются."
      submitLabel="Создать"
      pending={create.isPending}
      error={create.isError ? achievementErrorMessage(create.error) : null}
      canSubmit={code.trim() !== "" && name.trim() !== ""}
      onClose={onClose}
      onSubmit={() =>
        create.mutate(
          {
            code: code.trim(),
            name: name.trim(),
            description: description.trim() || null,
            source,
            award_method: awardMethod,
            repeatability,
          },
          {
            onSuccess: (definition) => {
              notify("success", `Достижение «${definition.name}» создано`);
              onClose();
            },
          },
        )
      }
    >
      <Input label="Код" value={code} onChange={(event) => setCode(event.target.value)} />
      <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} />
      <TextAreaField label="Описание" value={description} onChange={setDescription} />
      <SelectField
        label="Источник"
        value={source}
        options={optionsOf(SOURCE_LABELS)}
        onChange={(value) => setSource(value as AchievementSource)}
      />
      <SelectField
        label="Способ выдачи"
        value={awardMethod}
        options={optionsOf(DEFINITION_AWARD_METHOD_LABELS)}
        onChange={(value) => setAwardMethod(value as DefinitionAwardMethod)}
      />
      <SelectField
        label="Повторяемость"
        value={repeatability}
        options={optionsOf(REPEATABILITY_LABELS)}
        onChange={(value) => setRepeatability(value as Repeatability)}
        hint="Повторяемые достижения в текущей версии выдаются только вручную."
      />
    </FormDialog>
  );
}

// --- Normative sets --------------------------------------------------------------------

function NormativeSetsTab() {
  const [creating, setCreating] = useState(false);
  const query = useNormativeSets();

  return (
    <div>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          Версии внешних нормативов с источником. Использованная версия не изменяется — изменения
          оформляются новой версией.
        </p>
        <div className={styles.actions}>
          <Button variant="primary" icon="action.add" onClick={() => setCreating(true)}>
            Создать набор нормативов
          </Button>
        </div>
      </div>
      {query.isLoading ? <Loading label="Загружаем нормативы…" /> : null}
      {query.isError ? <ErrorState illustration="error" title="Не удалось загрузить нормативы" /> : null}
      {query.data && query.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Наборов нормативов пока нет" />
      ) : null}
      {query.data && query.data.items.length > 0 ? (
        <ul className={styles.list}>
          {query.data.items.map((set) => (
            <li key={set.id}>
              <Card data-testid="normative-set-card">
                <h3 className={styles.rowTitle}>
                  <Link className={styles.rowTitleLink} to={`/achievements/normative-sets/${set.id}`}>
                    {set.name}
                  </Link>
                </h3>
                <dl className={styles.meta}>
                  <Meta label="Код" value={set.code} />
                  {set.description ? <Meta label="Описание" value={set.description} /> : null}
                </dl>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
      {creating ? <CreateNormativeSetDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function CreateNormativeSetDialog({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateNormativeSet();
  const notify = useNotify();
  return (
    <FormDialog
      title="Новый набор нормативов"
      submitLabel="Создать"
      pending={create.isPending}
      error={create.isError ? achievementErrorMessage(create.error) : null}
      canSubmit={code.trim() !== "" && name.trim() !== ""}
      onClose={onClose}
      onSubmit={() =>
        create.mutate(
          { code: code.trim(), name: name.trim(), description: description.trim() || null },
          {
            onSuccess: (set) => {
              notify("success", `Набор нормативов «${set.name}» создан`);
              onClose();
            },
          },
        )
      }
    >
      <Input label="Код" value={code} onChange={(event) => setCode(event.target.value)} />
      <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} />
      <TextAreaField label="Описание" value={description} onChange={setDescription} />
    </FormDialog>
  );
}

// --- Awards ------------------------------------------------------------------------------

const AWARD_STATUS_OPTIONS = [
  { value: "", label: "Все" },
  { value: "active", label: "Действующие" },
  { value: "revoked", label: "Отозванные" },
];

const AWARD_METHOD_OPTIONS = [
  { value: "", label: "Все" },
  { value: "automatic", label: "Автоматические" },
  { value: "manual", label: "Ручные" },
];

export function AwardsTab({ definitionId }: { definitionId?: string }) {
  const [status, setStatus] = useState<AwardStatus | "">("");
  const [awardMethod, setAwardMethod] = useState<AwardMethod | "">("");
  const [page, setPage] = useState(1);
  const [awarding, setAwarding] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<AchievementAward | null>(null);
  const query = useAwards({ page, status, awardMethod, definitionId });

  return (
    <div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Состояние"
          value={status}
          options={AWARD_STATUS_OPTIONS}
          onChange={(value) => {
            setStatus(value as AwardStatus | "");
            setPage(1);
          }}
        />
        <FilterSelect
          label="Способ"
          value={awardMethod}
          options={AWARD_METHOD_OPTIONS}
          onChange={(value) => {
            setAwardMethod(value as AwardMethod | "");
            setPage(1);
          }}
        />
        <div className={styles.actions}>
          <Button variant="primary" icon="action.add" onClick={() => setAwarding(true)}>
            Выдать вручную
          </Button>
        </div>
      </div>

      {query.isLoading ? <Loading label="Загружаем выдачи…" /> : null}
      {query.isError ? <ErrorState illustration="error" title="Не удалось загрузить выдачи" /> : null}
      {query.data && query.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Выдач пока нет" />
      ) : null}
      {query.data && query.data.items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {query.data.items.map((award) => (
              <li key={award.id}>
                <AwardCard award={award} onRevoke={() => setRevokeTarget(award)} />
              </li>
            ))}
          </ul>
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}

      {awarding ? (
        <ManualAwardDialog definitionId={definitionId} onClose={() => setAwarding(false)} />
      ) : null}
      {revokeTarget ? (
        <RevokeAwardDialog award={revokeTarget} onClose={() => setRevokeTarget(null)} />
      ) : null}
    </div>
  );
}

function AwardCard({ award, onRevoke }: { award: AchievementAward; onRevoke: () => void }) {
  const provenance =
    award.award_method === "automatic"
      ? `${AWARD_METHOD_LABELS.automatic}${award.evaluation_trigger ? `, ${TRIGGER_LABELS[award.evaluation_trigger]}` : ""}`
      : AWARD_METHOD_LABELS.manual;
  return (
    <Card data-testid="award-card">
      <div className={styles.rowHeader}>
        <h3 className={styles.rowTitle}>
          {award.person_name} — {award.definition_name}
        </h3>
        <StatusBadge status={awardStatusIcon(award.status)} label={AWARD_STATUS_LABELS[award.status]} />
      </div>
      <dl className={styles.meta}>
        <Meta label="Выдано" value={formatDateTime(award.awarded_at)} />
        <Meta label="Способ" value={provenance} />
        <Meta
          label="Версия правила"
          value={award.rule_version_number !== null ? `v${award.rule_version_number}` : "—"}
        />
        {award.normative_version_number !== null ? (
          <Meta label="Версия нормативов" value={`v${award.normative_version_number}`} />
        ) : null}
        {award.evaluated_metrics ? (
          <Meta
            label="Показатели при расчёте"
            value={Object.entries(award.evaluated_metrics)
              .map(([metric, value]) => `${metric}: ${value ?? "—"}`)
              .join(", ")}
          />
        ) : null}
        {award.verification_note ? <Meta label="Проверка" value={award.verification_note} /> : null}
        {award.status === "revoked" ? (
          <>
            <Meta label="Отозвано" value={formatDateTime(award.revoked_at)} />
            <Meta label="Причина отзыва" value={award.revocation_reason ?? "—"} />
          </>
        ) : null}
      </dl>
      {award.status === "active" ? (
        <div className={styles.cardActions}>
          <Button variant="destructive" onClick={onRevoke}>
            Отозвать
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

function ManualAwardDialog({ definitionId, onClose }: { definitionId?: string; onClose: () => void }) {
  const [selectedDefinition, setSelectedDefinition] = useState(definitionId ?? "");
  const [search, setSearch] = useState("");
  const [personId, setPersonId] = useState("");
  const [ruleVersionId, setRuleVersionId] = useState("");
  const [note, setNote] = useState("");
  const definitions = useAllActiveDefinitions();
  const ruleVersions = useRuleVersions(selectedDefinition || undefined);
  const persons = usePersons({ page: 1, search, enabled: search.trim().length > 0 });
  const create = useCreateManualAward();
  const notify = useNotify();

  return (
    <FormDialog
      title="Выдать достижение вручную"
      description="Ручная выдача фиксируется как проверенная администратором и не выдаётся за автоматический расчёт."
      submitLabel="Выдать"
      pending={create.isPending}
      error={create.isError ? achievementErrorMessage(create.error) : null}
      canSubmit={selectedDefinition !== "" && personId !== ""}
      onClose={onClose}
      onSubmit={() =>
        create.mutate(
          {
            definition_id: selectedDefinition,
            person_id: personId,
            rule_version_id: ruleVersionId || null,
            verification_note: note.trim() || null,
          },
          {
            onSuccess: (award) => {
              notify("success", `Достижение «${award.definition_name}» выдано: ${award.person_name}`);
              onClose();
            },
          },
        )
      }
    >
      <SelectField
        label="Достижение"
        value={selectedDefinition}
        placeholder="Выберите достижение"
        options={(definitions.data?.items ?? []).map((definition) => ({
          value: definition.id,
          label: definition.name,
        }))}
        onChange={(value) => {
          setSelectedDefinition(value);
          setRuleVersionId("");
        }}
      />
      <SelectField
        label="Проверено по версии правила"
        value={ruleVersionId}
        placeholder="Без привязки к версии правила"
        options={(ruleVersions.data?.items ?? []).map((version) => ({
          value: version.id,
          label: `Версия ${version.version_number} (${LIFECYCLE_LABELS[version.status]})`,
        }))}
        onChange={setRuleVersionId}
        hint="Указывайте версию, только если выдача проверена именно по ней. Активная версия не подставляется автоматически."
      />
      <Input
        label="Поиск участника"
        value={search}
        onChange={(event) => {
          setSearch(event.target.value);
          setPersonId("");
        }}
      />
      <SelectField
        label="Участник"
        value={personId}
        placeholder={search.trim() ? "Выберите участника" : "Сначала введите имя для поиска"}
        options={(persons.data?.items ?? []).map((person) => ({
          value: person.id,
          label: personFullName(person),
        }))}
        onChange={setPersonId}
      />
      <TextAreaField
        label="Основание / результат проверки"
        value={note}
        onChange={setNote}
        hint="Например, проверенная маршрутная книжка. Для ФСТР и проверки по нормативам обязательно."
      />
    </FormDialog>
  );
}

function RevokeAwardDialog({ award, onClose }: { award: AchievementAward; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const revoke = useRevokeAward();
  const notify = useNotify();
  return (
    <FormDialog
      title="Отозвать достижение"
      description={`${award.person_name} — ${award.definition_name}. Запись о выдаче сохранится в истории со статусом «Отозвано».`}
      submitLabel="Отозвать"
      destructive
      pending={revoke.isPending}
      error={revoke.isError ? achievementErrorMessage(revoke.error) : null}
      canSubmit={reason.trim() !== ""}
      onClose={onClose}
      onSubmit={() =>
        revoke.mutate(
          { awardId: award.id, reason: reason.trim() },
          {
            onSuccess: () => {
              notify("success", "Выдача отозвана");
              onClose();
            },
          },
        )
      }
    >
      <TextAreaField label="Причина отзыва" value={reason} onChange={setReason} />
    </FormDialog>
  );
}
