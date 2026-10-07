import { useState } from "react";

import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Stepper } from "../components/ui/Stepper";
import { Loading } from "../components/ui/Loading";
import { ErrorState } from "../components/ui/ErrorState";
import { EmptyState } from "../components/ui/EmptyState";
import { Pagination } from "../components/ui/Pagination";
import { useNotify } from "../components/ui/notificationContext";
import { printHtmlBlob, saveBlob } from "../api/client";
import {
  useExportFields,
  useExportFilters,
  useParticipantExportPreview,
  useRunParticipantExport,
  type ExportContext,
  type ExportField,
  type ExportFormat,
  type ParticipantExportSelection,
} from "../api/exports";
import {
  EVENT_CONTEXTS,
  FORMAT_OPTIONS,
  GROUP_CONTEXTS,
  exportErrorMessage,
  membershipStatusOptions,
  orderedFieldCodes,
  selectionRequest,
} from "../domain/participantExport";
import { ContextStep, FiltersStep, type SelectedEvent } from "./ParticipantExportSteps";
import styles from "./ImportExport.module.css";

/**
 * «Отчёты → Участники мероприятий» (Issue #299; docs/04-ux/
 * import-export-ui.md §3.3, information-architecture.md §16).
 *
 * context → filters → preview → XLSX/PDF/Print. The report is a view of
 * the one canonical Participant Export dataset, not a second selection
 * mechanism: the preview is a backend page of
 * `POST /memberships/exports/preview` and every format is
 * `POST /memberships/exports` with the very same selection. Contexts,
 * filters and the participation statuses (`GET /memberships/exports/
 * filters`) are the export's own; the backend resolves, filters, orders,
 * counts and authorizes everything — nothing is filtered or re-paged here.
 */

const REPORT_STEPS = ["Контекст", "Фильтры", "Просмотр и выгрузка"] as const;

/** The report's minimum preview (import-export-ui.md §3.3): ФИО, группа,
 * мероприятие, дата/время, статус регистрации — named by their existing
 * allowlist codes. Only codes the backend offers for the chosen context
 * are sent (a Group column exists only where a Group is part of the
 * context, Event columns only where an Event is). */
const REPORT_BASE_FIELD_CODES: readonly string[] = [
  "person.last_name",
  "person.first_name",
  "person.middle_name",
  "group.name",
  "event.name",
  "event.starts_at",
  "event_participation.status",
];

const PREVIEW_PAGE_SIZE = 50;

export function EventParticipantsReportPage() {
  const [step, setStep] = useState(0);
  const [context, setContext] = useState<ExportContext | null>(null);
  const [groupId, setGroupId] = useState("");
  const [event, setEvent] = useState<SelectedEvent | null>(null);
  const [eventId, setEventId] = useState("");
  const [membershipStatus, setMembershipStatus] = useState("active");
  const [participationStatus, setParticipationStatus] = useState("");
  const [extraFields, setExtraFields] = useState<string[]>([]);
  const [page, setPage] = useState(1);

  const fieldsQuery = useExportFields();
  const runExport = useRunParticipantExport();
  const notify = useNotify();

  const needsGroup = context !== null && GROUP_CONTEXTS.has(context);
  const needsEvent = context !== null && EVENT_CONTEXTS.has(context);
  // Backend-authoritative participation statuses, as in the export master:
  // no frontend list, no fallback.
  const filtersQuery = useExportFilters(needsEvent);

  const availableFields: ExportField[] =
    context && fieldsQuery.data
      ? fieldsQuery.data.items.filter((field) => field.contexts.includes(context))
      : [];
  const baseFields = availableFields.filter((field) => REPORT_BASE_FIELD_CODES.includes(field.field_code));
  const optionalFields = availableFields.filter((field) => !REPORT_BASE_FIELD_CODES.includes(field.field_code));

  const filtersComplete =
    (!needsGroup || groupId !== "") && (!needsEvent || (eventId !== "" && filtersQuery.isSuccess));
  const stepComplete = [context !== null, filtersComplete, true];

  const selection: ParticipantExportSelection | null =
    context && filtersComplete && baseFields.length > 0
      ? selectionRequest(
          { context, groupId, eventId, membershipStatus, participationStatus },
          orderedFieldCodes(availableFields, [
            ...baseFields.map((field) => field.field_code),
            ...extraFields,
          ]),
        )
      : null;
  const previewQuery = useParticipantExportPreview(step === 2 ? selection : null, page, PREVIEW_PAGE_SIZE);

  const chooseContext = (next: ExportContext) => {
    setContext(next);
    setPage(1);
    runExport.reset();
    if (fieldsQuery.data) {
      const allowed = new Set(
        fieldsQuery.data.items
          .filter((field) => field.contexts.includes(next))
          .map((field) => field.field_code),
      );
      setExtraFields((current) => current.filter((code) => allowed.has(code)));
    }
    if (!membershipStatusOptions(next).some((option) => option.value === membershipStatus)) {
      setMembershipStatus("active");
    }
  };

  const toggleExtraField = (code: string, checked: boolean) => {
    setPage(1);
    setExtraFields((current) => (checked ? [...current, code] : current.filter((item) => item !== code)));
  };

  const goToStep = (next: number) => {
    // A changed context/filter selection always starts on its first page.
    if (next === 2) setPage(1);
    setStep(next);
  };

  const startExport = (format: ExportFormat) => {
    if (!selection) return;
    runExport.mutate(
      { ...selection, format },
      {
        onSuccess: ({ blob, filename }) => {
          if (format === "print") {
            printHtmlBlob(blob);
          } else {
            saveBlob(blob, filename ?? `participants.${format}`);
          }
          notify("success", "Отчёт сформирован");
        },
        onError: (error) => notify("error", exportErrorMessage(error)),
      },
    );
  };

  if (fieldsQuery.isError && fieldsQuery.error.status === 403) {
    return (
      <div>
        <PageHeader title="Участники мероприятий" back={{ to: "/reports", label: "Отчёты" }} />
        <ErrorState
          illustration="403"
          title="Отчёт недоступен"
          description="У вас нет прав на отчёт по участникам."
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Участники мероприятий"
        description="Проверьте, кто зарегистрирован на мероприятие: выберите контекст и фильтры, просмотрите список и выгрузите его."
        back={{ to: "/reports", label: "Отчёты" }}
      />

      <Stepper label="Шаги отчёта" steps={REPORT_STEPS} current={step} />

      <Card className={styles.panel}>
        {step === 0 ? <ContextStep context={context} onChange={chooseContext} /> : null}

        {step === 1 && context ? (
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

        {step === 2 ? (
          <div className={styles.panel}>
            <ReportFieldsChooser
              fieldsQuery={fieldsQuery}
              optional={optionalFields}
              selected={extraFields}
              onToggle={toggleExtraField}
            />
            <ReportPreview previewQuery={previewQuery} onPageChange={setPage} />
            <div className={styles.actionsStart} role="group" aria-label="Выгрузка отчёта">
              {FORMAT_OPTIONS.map((option) => (
                <Button
                  key={option.value}
                  variant={option.value === "xlsx" ? "primary" : "secondary"}
                  icon={option.value === "print" ? undefined : "action.download"}
                  disabled={!selection || runExport.isPending}
                  title={option.description}
                  onClick={() => startExport(option.value)}
                >
                  {option.title}
                </Button>
              ))}
            </div>
            {runExport.isPending ? <Loading label="Формируем отчёт…" /> : null}
          </div>
        ) : null}

        <div className={styles.actions}>
          {step > 0 ? (
            <Button
              variant="secondary"
              icon="action.back"
              onClick={() => goToStep(step - 1)}
              disabled={runExport.isPending}
            >
              Назад
            </Button>
          ) : null}
          {step < REPORT_STEPS.length - 1 ? (
            <Button
              variant="primary"
              icon="action.forward"
              disabled={!stepComplete[step]}
              onClick={() => goToStep(step + 1)}
            >
              Далее
            </Button>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

function ReportFieldsChooser({
  fieldsQuery,
  optional,
  selected,
  onToggle,
}: {
  fieldsQuery: ReturnType<typeof useExportFields>;
  optional: ExportField[];
  selected: string[];
  onToggle: (code: string, checked: boolean) => void;
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
  if (optional.length === 0) return null;
  return (
    <details>
      <summary>Дополнительные поля</summary>
      <fieldset className={`${styles.optionList} ${styles.fieldset}`}>
        <legend className={styles.legend}>Добавить в отчёт</legend>
        {optional.map((field) => (
          <label key={field.field_code} className={styles.option}>
            <input
              type="checkbox"
              checked={selected.includes(field.field_code)}
              onChange={(event) => onToggle(field.field_code, event.target.checked)}
            />
            <span className={styles.optionTitle}>{field.label}</span>
          </label>
        ))}
      </fieldset>
    </details>
  );
}

function ReportPreview({
  previewQuery,
  onPageChange,
}: {
  previewQuery: ReturnType<typeof useParticipantExportPreview>;
  onPageChange: (page: number) => void;
}) {
  if (previewQuery.isPending && previewQuery.fetchStatus === "fetching") {
    return <Loading label="Формируем предпросмотр…" />;
  }
  if (previewQuery.isError) {
    return (
      <ErrorState
        illustration={previewQuery.error.status === 403 ? "403" : previewQuery.error.status === 404 ? "404" : "error"}
        title="Не удалось сформировать предпросмотр"
        description={exportErrorMessage(previewQuery.error)}
        action={
          previewQuery.error.status !== 403 && previewQuery.error.status !== 404 ? (
            <Button variant="secondary" onClick={() => void previewQuery.refetch()}>
              Повторить
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (!previewQuery.data) return null;

  const { title, columns, items, pagination } = previewQuery.data;
  return (
    <section aria-label="Предпросмотр отчёта">
      <h2 className={styles.sectionTitle}>{title}</h2>
      <p className={styles.muted} role="status">
        Найдено: {pagination.total}
      </p>
      {pagination.total === 0 ? (
        <EmptyState
          illustration="no-results"
          title="Участники не найдены"
          description="По выбранному контексту и фильтрам записей нет."
        />
      ) : (
        <>
          <div className={styles.previewTableWrap}>
            <table className={styles.previewTable}>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.field_code} scope="col">
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row, rowIndex) => (
                  <tr key={(pagination.page - 1) * pagination.page_size + rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={columns[cellIndex]?.field_code ?? cellIndex}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={pagination.page}
            pages={pagination.pages}
            total={pagination.total}
            onPageChange={onPageChange}
          />
        </>
      )}
    </section>
  );
}
