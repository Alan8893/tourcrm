import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Stepper } from "../components/ui/Stepper";
import { Loading } from "../components/ui/Loading";
import { ErrorState } from "../components/ui/ErrorState";
import { FilterSelect, type FilterOption } from "../components/ui/FilterSelect";
import { SearchInput } from "../components/ui/SearchInput";
import { useNotify } from "../components/ui/notificationContext";
import { printHtmlBlob, saveBlob, type ApiError } from "../api/client";
import {
  useExportFields,
  useExportFilters,
  useRunParticipantExport,
  type ExportContext,
  type ExportField,
  type ExportFilterOption,
  type ExportFormat,
  type ParticipantExportRequest,
} from "../api/exports";
import { useGroups } from "../api/groups";
import { useEvent, useEventSearch } from "../api/events";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { membershipStatusLabel, type MembershipStatus } from "../domain/statusMapping";
import styles from "./ImportExport.module.css";

/**
 * Participant Export master — «Отчёты → Экспорт» (TH-0118.5,
 * docs/04-ux/import-export-ui.md §3.2, §4; docs/05-api/
 * participant-export-api.md).
 *
 * dataset (context) → fields → filters → format → export. The wizard only
 * collects the administrator's choices and sends them to the one existing
 * `POST /memberships/exports`; the backend resolves the dataset from the
 * context (including the Group ∩ Event case), re-validates every field and
 * filter and authorizes the request. The field list comes from
 * `GET /memberships/exports/fields` — no frontend copy of the allowlist.
 * XLSX, PDF and Print send the identical request and differ only in
 * `format`.
 *
 * Contextual entry points (Group / Event) open this same wizard with the
 * context pre-selected via `?context=…&group_id=…&event_id=…`.
 */

const EXPORT_STEPS = ["Что выгружаем?", "Поля", "Фильтры", "Формат", "Экспорт"] as const;

const CONTEXT_OPTIONS: readonly { value: ExportContext; title: string; description: string }[] = [
  {
    value: "club",
    title: "Все участники",
    description: "Участники клуба с выбранным статусом членства.",
  },
  { value: "group", title: "Участники группы", description: "Состав выбранной группы." },
  { value: "event", title: "Участники события", description: "Участники выбранного события." },
  {
    value: "group_event",
    title: "Группа на событии",
    description: "Участники выбранной группы, относящиеся к выбранному событию.",
  },
];

const FORMAT_OPTIONS: readonly { value: ExportFormat; title: string; description: string }[] = [
  { value: "xlsx", title: "XLSX", description: "Редактируемая таблица Excel." },
  { value: "pdf", title: "PDF", description: "Готовый к рассылке и печати документ." },
  { value: "print", title: "Печать", description: "Открыть системный диалог печати." },
];

/** Contexts carrying a Group / an Event target (export API §3). Used only
 * to show the matching filter controls and to send only applicable keys —
 * the backend re-validates every combination. */
const GROUP_CONTEXTS: ReadonlySet<ExportContext> = new Set(["group", "group_event"]);
const EVENT_CONTEXTS: ReadonlySet<ExportContext> = new Set(["event", "group_event"]);

const CLUB_MEMBERSHIP_STATUSES: readonly MembershipStatus[] = [
  "active",
  "pending",
  "suspended",
  "inactive",
  "archived",
];

const GROUP_MEMBERSHIP_STATUS_OPTIONS: FilterOption[] = [
  { value: "active", label: "Активные" },
  { value: "ended", label: "Завершённые" },
];

/** "No participation filter" — the request then omits
 * `participation_status` and the backend returns every status. Not a
 * business value; every real status comes from
 * `GET /memberships/exports/filters`. */
const ANY_PARTICIPATION_STATUS: FilterOption = { value: "", label: "Любой статус" };

function membershipStatusOptions(context: ExportContext): FilterOption[] {
  return GROUP_CONTEXTS.has(context)
    ? GROUP_MEMBERSHIP_STATUS_OPTIONS
    : CLUB_MEMBERSHIP_STATUSES.map((status) => ({
        value: status,
        label: membershipStatusLabel(status),
      }));
}

function isExportContext(value: string | null): value is ExportContext {
  return CONTEXT_OPTIONS.some((option) => option.value === value);
}

function exportErrorMessage(error: ApiError): string {
  if (error.status === 403) return "У вас нет прав на этот экспорт.";
  if (error.status === 404) return "Выбранная группа или событие не найдены.";
  return error.message;
}

type SelectedEvent = { id: string; title: string; start_at: string };

function formatEventDate(value: string): string {
  return new Date(value).toLocaleDateString("ru-RU");
}

export function ExportPage() {
  const [searchParams] = useSearchParams();
  const initialContextParam = searchParams.get("context");
  const initialContext = isExportContext(initialContextParam) ? initialContextParam : null;
  const initialEventId = searchParams.get("event_id") ?? "";

  const [step, setStep] = useState(initialContext ? 1 : 0);
  const [context, setContext] = useState<ExportContext | null>(initialContext);
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [groupId, setGroupId] = useState(searchParams.get("group_id") ?? "");
  const [event, setEvent] = useState<SelectedEvent | null>(null);
  const [eventId, setEventId] = useState(initialEventId);
  const [membershipStatus, setMembershipStatus] = useState("active");
  const [participationStatus, setParticipationStatus] = useState("");
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [completed, setCompleted] = useState<ExportFormat | null>(null);

  const fieldsQuery = useExportFields();
  const runExport = useRunParticipantExport();
  const notify = useNotify();

  const availableFields: ExportField[] =
    context && fieldsQuery.data
      ? fieldsQuery.data.items.filter((field) => field.contexts.includes(context))
      : [];

  const chooseContext = (next: ExportContext) => {
    setContext(next);
    setCompleted(null);
    runExport.reset();
    if (fieldsQuery.data) {
      const allowed = new Set(
        fieldsQuery.data.items
          .filter((field) => field.contexts.includes(next))
          .map((field) => field.field_code),
      );
      setSelectedFields((current) => current.filter((code) => allowed.has(code)));
    }
    if (!membershipStatusOptions(next).some((option) => option.value === membershipStatus)) {
      setMembershipStatus("active");
    }
  };

  const needsGroup = context !== null && GROUP_CONTEXTS.has(context);
  const needsEvent = context !== null && EVENT_CONTEXTS.has(context);
  // Backend-authoritative participation statuses — loaded only when an
  // Event context can use them. Without them the filters step cannot be
  // completed: no fallback list is ever substituted.
  const filtersQuery = useExportFilters(needsEvent);

  const stepComplete = [
    context !== null,
    selectedFields.length > 0,
    (!needsGroup || groupId !== "") &&
      (!needsEvent || (eventId !== "" && filtersQuery.isSuccess)),
    true,
    true,
  ];

  const buildRequest = (): ParticipantExportRequest | null => {
    if (!context) return null;
    // Preserve the backend's own field order for the selected columns.
    const orderedFields = availableFields
      .map((field) => field.field_code)
      .filter((code) => selectedFields.includes(code));
    return {
      context,
      fields: orderedFields,
      format,
      membership_status: membershipStatus,
      ...(needsGroup ? { group_id: groupId } : {}),
      ...(needsEvent ? { event_id: eventId } : {}),
      ...(needsEvent && participationStatus ? { participation_status: participationStatus } : {}),
    };
  };

  const startExport = () => {
    const request = buildRequest();
    if (!request) return;
    setCompleted(null);
    runExport.mutate(request, {
      onSuccess: ({ blob, filename }) => {
        if (request.format === "print") {
          printHtmlBlob(blob);
        } else {
          saveBlob(blob, filename ?? `participants.${request.format}`);
        }
        setCompleted(request.format);
        notify("success", "Экспорт сформирован");
      },
      onError: (error) => notify("error", exportErrorMessage(error)),
    });
  };

  if (fieldsQuery.isError && fieldsQuery.error.status === 403) {
    return (
      <div>
        <PageHeader title="Экспорт участников" back={{ to: "/reports", label: "Отчёты" }} />
        <ErrorState
          illustration="403"
          title="Экспорт недоступен"
          description="У вас нет прав на экспорт участников."
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Экспорт участников"
        description="Сформируйте список участников под конкретную задачу: выберите данные, поля, фильтры и формат."
        back={{ to: "/reports", label: "Отчёты" }}
      />

      <Stepper label="Шаги экспорта" steps={EXPORT_STEPS} current={step} />

      <Card className={styles.panel}>
        {step === 0 ? <ContextStep context={context} onChange={chooseContext} /> : null}

        {step === 1 ? (
          <FieldsStep
            fieldsQuery={fieldsQuery}
            available={availableFields}
            selected={selectedFields}
            onChange={setSelectedFields}
          />
        ) : null}

        {step === 2 && context ? (
          <FiltersStep
            context={context}
            groupId={groupId}
            onGroupChange={setGroupId}
            event={event}
            eventId={eventId}
            onEventChange={(next) => {
              setEvent(next);
              setEventId(next?.id ?? "");
            }}
            membershipStatus={membershipStatus}
            onMembershipStatusChange={setMembershipStatus}
            participationStatus={participationStatus}
            onParticipationStatusChange={setParticipationStatus}
            filtersQuery={filtersQuery}
          />
        ) : null}

        {step === 3 ? (
          <FormatStep
            format={format}
            onChange={(next) => {
              setFormat(next);
              setCompleted(null);
            }}
          />
        ) : null}

        {step === 4 && context ? (
          <SummaryStep
            context={context}
            fields={availableFields.filter((field) => selectedFields.includes(field.field_code))}
            groupId={groupId}
            event={event}
            eventId={eventId}
            membershipStatus={membershipStatus}
            participationStatus={participationStatus}
            participationOptions={filtersQuery.data?.participation_status ?? []}
            format={format}
            pending={runExport.isPending}
            error={runExport.error}
            completed={completed}
          />
        ) : null}

        <div className={styles.actions}>
          {step > 0 ? (
            <Button
              variant="secondary"
              icon="action.back"
              onClick={() => setStep(step - 1)}
              disabled={runExport.isPending}
            >
              Назад
            </Button>
          ) : null}
          {step < EXPORT_STEPS.length - 1 ? (
            <Button
              variant="primary"
              icon="action.forward"
              disabled={!stepComplete[step]}
              onClick={() => setStep(step + 1)}
            >
              Далее
            </Button>
          ) : (
            <Button
              variant="primary"
              icon="action.download"
              disabled={runExport.isPending}
              onClick={startExport}
            >
              {runExport.isPending
                ? "Формируем…"
                : format === "print"
                  ? "Открыть печать"
                  : "Экспортировать"}
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

// --- Step 1: dataset ---------------------------------------------------------

function ContextStep({
  context,
  onChange,
}: {
  context: ExportContext | null;
  onChange: (context: ExportContext) => void;
}) {
  return (
    <fieldset className={styles.optionList}>
      <legend className={styles.legend}>Что выгружаем?</legend>
      {CONTEXT_OPTIONS.map((option) => (
        <label key={option.value} className={styles.option}>
          <input
            type="radio"
            name="export-context"
            value={option.value}
            checked={context === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className={styles.optionText}>
            <span className={styles.optionTitle}>{option.title}</span>
            <span className={styles.optionDescription}>{option.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

// --- Step 2: fields ------------------------------------------------------------

function FieldsStep({
  fieldsQuery,
  available,
  selected,
  onChange,
}: {
  fieldsQuery: ReturnType<typeof useExportFields>;
  available: ExportField[];
  selected: string[];
  onChange: (fields: string[]) => void;
}) {
  if (fieldsQuery.isLoading) return <Loading label="Загружаем доступные поля…" />;
  if (fieldsQuery.isError) {
    return (
      <ErrorState
        illustration="error"
        title="Не удалось загрузить поля"
        description={fieldsQuery.error.message}
        action={
          <Button variant="secondary" onClick={() => void fieldsQuery.refetch()}>
            Повторить
          </Button>
        }
      />
    );
  }
  if (available.length === 0) {
    return <p className={styles.muted}>Для выбранных данных нет доступных полей.</p>;
  }

  const toggle = (code: string, checked: boolean) =>
    onChange(checked ? [...selected, code] : selected.filter((item) => item !== code));

  return (
    <fieldset className={`${styles.panel} ${styles.fieldset}`}>
      <legend className={styles.legend}>Какие поля включить?</legend>
      <div className={styles.fieldToolbar}>
        <span className={styles.muted}>
          Выбрано: {selected.length} из {available.length}
        </span>
        <div className={styles.actionsStart}>
          <Button
            variant="secondary"
            type="button"
            onClick={() => onChange(available.map((field) => field.field_code))}
          >
            Выбрать все
          </Button>
          <Button variant="secondary" type="button" onClick={() => onChange([])}>
            Снять все
          </Button>
        </div>
      </div>
      <div className={styles.optionList}>
        {available.map((field) => (
          <label key={field.field_code} className={styles.option}>
            <input
              type="checkbox"
              checked={selected.includes(field.field_code)}
              onChange={(event) => toggle(field.field_code, event.target.checked)}
            />
            <span className={styles.optionTitle}>{field.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

// --- Step 3: filters -----------------------------------------------------------

function FiltersStep({
  context,
  groupId,
  onGroupChange,
  event,
  eventId,
  onEventChange,
  membershipStatus,
  onMembershipStatusChange,
  participationStatus,
  onParticipationStatusChange,
  filtersQuery,
}: {
  context: ExportContext;
  groupId: string;
  onGroupChange: (groupId: string) => void;
  event: SelectedEvent | null;
  eventId: string;
  onEventChange: (event: SelectedEvent | null) => void;
  membershipStatus: string;
  onMembershipStatusChange: (value: string) => void;
  participationStatus: string;
  onParticipationStatusChange: (value: string) => void;
  filtersQuery: ReturnType<typeof useExportFilters>;
}) {
  const inGroupContext = GROUP_CONTEXTS.has(context);
  const inEventContext = EVENT_CONTEXTS.has(context);

  return (
    <div className={styles.panel}>
      <h2 className={styles.sectionTitle}>Фильтры</h2>
      <div className={styles.filters}>
        {inGroupContext ? <GroupFilter groupId={groupId} onChange={onGroupChange} /> : null}
        <FilterSelect
          label={inGroupContext ? "Статус в группе" : "Статус членства в клубе"}
          value={membershipStatus}
          options={membershipStatusOptions(context)}
          onChange={onMembershipStatusChange}
        />
        {inEventContext ? (
          <ParticipationStatusFilter
            filtersQuery={filtersQuery}
            value={participationStatus}
            onChange={onParticipationStatusChange}
          />
        ) : null}
      </div>
      {inEventContext ? (
        <EventFilter event={event} eventId={eventId} onChange={onEventChange} />
      ) : null}
    </div>
  );
}

function ParticipationStatusFilter({
  filtersQuery,
  value,
  onChange,
}: {
  filtersQuery: ReturnType<typeof useExportFilters>;
  value: string;
  onChange: (value: string) => void;
}) {
  if (filtersQuery.isLoading) return <Loading label="Загружаем статусы участия…" />;
  if (filtersQuery.isError) {
    return (
      <div className={`${styles.notice} ${styles.noticeError}`} role="alert">
        <p className={styles.muted}>
          Не удалось загрузить статусы участия: {filtersQuery.error.message}. Продолжить экспорт
          по событию можно после повторной загрузки.
        </p>
        <Button variant="secondary" onClick={() => void filtersQuery.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }
  if (!filtersQuery.data) return null;
  return (
    <FilterSelect
      label="Статус участия в событии"
      value={value}
      options={[ANY_PARTICIPATION_STATUS, ...filtersQuery.data.participation_status]}
      onChange={onChange}
    />
  );
}

function GroupFilter({ groupId, onChange }: { groupId: string; onChange: (id: string) => void }) {
  const groupsQuery = useGroups();

  if (groupsQuery.isLoading) return <Loading label="Загружаем группы…" />;
  if (groupsQuery.isError) {
    return (
      <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
        Не удалось загрузить группы: {groupsQuery.error.message}
      </p>
    );
  }
  const groups = groupsQuery.data?.items ?? [];
  if (groups.length === 0) {
    return <p className={styles.muted}>В клубе пока нет групп.</p>;
  }
  return (
    <FilterSelect
      label="Группа"
      value={groupId}
      options={[
        { value: "", label: "Выберите группу" },
        ...groups.map((group) => ({
          value: group.id,
          label: group.status === "archived" ? `${group.name} (архив)` : group.name,
        })),
      ]}
      onChange={onChange}
    />
  );
}

function EventFilter({
  event,
  eventId,
  onChange,
}: {
  event: SelectedEvent | null;
  eventId: string;
  onChange: (event: SelectedEvent | null) => void;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const eventsQuery = useEventSearch(debouncedSearch);
  // A contextual entry point passes only `event_id`; its title is read
  // from the existing Event detail endpoint for display.
  const preselectedQuery = useEvent(eventId && !event ? eventId : undefined);
  const selectedTitle = event?.title ?? preselectedQuery.data?.title;

  return (
    <div className={styles.panel}>
      <p className={styles.muted} aria-live="polite">
        Событие:{" "}
        <strong>
          {eventId ? (selectedTitle ?? "загружаем…") : "не выбрано"}
        </strong>
      </p>
      <SearchInput
        label="Найти событие"
        value={search}
        onChange={setSearch}
        placeholder="Название события"
      />
      {eventsQuery.isLoading ? <Loading label="Ищем события…" /> : null}
      {eventsQuery.isError ? (
        <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
          Не удалось загрузить события: {eventsQuery.error.message}
        </p>
      ) : null}
      {eventsQuery.data ? (
        <ul className={styles.pickerList} aria-label="События">
          {eventsQuery.data.items.length === 0 ? (
            <li className={styles.pickerEmpty}>Событий не найдено.</li>
          ) : (
            eventsQuery.data.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`${styles.pickerItem} ${item.id === eventId ? styles.pickerItemSelected : ""}`}
                  aria-pressed={item.id === eventId}
                  onClick={() => onChange({ id: item.id, title: item.title, start_at: item.start_at })}
                >
                  {item.title} · {formatEventDate(item.start_at)}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

// --- Step 4: format ------------------------------------------------------------

function FormatStep({
  format,
  onChange,
}: {
  format: ExportFormat;
  onChange: (format: ExportFormat) => void;
}) {
  return (
    <fieldset className={styles.optionList}>
      <legend className={styles.legend}>Формат</legend>
      {FORMAT_OPTIONS.map((option) => (
        <label key={option.value} className={styles.option}>
          <input
            type="radio"
            name="export-format"
            value={option.value}
            checked={format === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className={styles.optionText}>
            <span className={styles.optionTitle}>{option.title}</span>
            <span className={styles.optionDescription}>{option.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

// --- Step 5: summary + export --------------------------------------------------

function SummaryStep({
  context,
  fields,
  groupId,
  event,
  eventId,
  membershipStatus,
  participationStatus,
  participationOptions,
  format,
  pending,
  error,
  completed,
}: {
  context: ExportContext;
  fields: ExportField[];
  groupId: string;
  event: SelectedEvent | null;
  eventId: string;
  membershipStatus: string;
  participationStatus: string;
  participationOptions: ExportFilterOption[];
  format: ExportFormat;
  pending: boolean;
  error: ApiError | null;
  completed: ExportFormat | null;
}) {
  const groupsQuery = useGroups();
  const preselectedQuery = useEvent(EVENT_CONTEXTS.has(context) && !event ? eventId : undefined);
  const contextOption = CONTEXT_OPTIONS.find((option) => option.value === context);
  const groupName = groupsQuery.data?.items.find((group) => group.id === groupId)?.name;
  const eventTitle = event?.title ?? preselectedQuery.data?.title;
  const statusLabel =
    membershipStatusOptions(context).find((option) => option.value === membershipStatus)?.label ??
    membershipStatus;
  const participationLabel =
    [ANY_PARTICIPATION_STATUS, ...participationOptions].find(
      (option) => option.value === participationStatus,
    )?.label ?? participationStatus;

  return (
    <div className={styles.panel}>
      <h2 className={styles.sectionTitle}>Проверьте параметры экспорта</h2>
      <dl className={styles.summary} aria-label="Параметры экспорта">
        <dt>Данные</dt>
        <dd>{contextOption?.title}</dd>
        {GROUP_CONTEXTS.has(context) ? (
          <>
            <dt>Группа</dt>
            <dd>{groupName ? `«${groupName}»` : "…"}</dd>
          </>
        ) : null}
        {EVENT_CONTEXTS.has(context) ? (
          <>
            <dt>Событие</dt>
            <dd>{eventTitle ? `«${eventTitle}»` : "…"}</dd>
          </>
        ) : null}
        <dt>Поля</dt>
        <dd>{fields.map((field) => field.label).join(", ")}</dd>
        <dt>{GROUP_CONTEXTS.has(context) ? "Статус в группе" : "Статус членства"}</dt>
        <dd>{statusLabel}</dd>
        {EVENT_CONTEXTS.has(context) ? (
          <>
            <dt>Статус участия</dt>
            <dd>{participationLabel}</dd>
          </>
        ) : null}
        <dt>Формат</dt>
        <dd>{FORMAT_OPTIONS.find((option) => option.value === format)?.title}</dd>
      </dl>
      {pending ? <Loading label="Формируем выгрузку…" /> : null}
      {error ? (
        <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
          Не удалось сформировать экспорт: {exportErrorMessage(error)}
        </p>
      ) : null}
      {completed ? (
        <p className={styles.notice} role="status">
          {completed === "print"
            ? "Документ для печати сформирован — откроется системный диалог печати."
            : "Файл сформирован и сохранён."}
        </p>
      ) : null}
    </div>
  );
}
