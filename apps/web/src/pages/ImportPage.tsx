import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import buttonStyles from "../components/ui/Button.module.css";
import { Icon } from "../components/ui/Icon";
import { Stepper } from "../components/ui/Stepper";
import { Loading } from "../components/ui/Loading";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Pagination } from "../components/ui/Pagination";
import { useNotify } from "../components/ui/notificationContext";
import type { ApiError } from "../api/client";
import {
  useApplyImport,
  useApproveImport,
  useImportJob,
  useImportJobIssues,
  usePreviewImport,
  useUploadImportFile,
  type ImportIssueSeverity,
  type ImportJob,
  type ImportJobIssue,
  type ImportJobStatus,
} from "../api/imports";
import type { StatusIconId } from "../assets/icons";
import styles from "./ImportExport.module.css";

/** Downloadable import templates (Issue #228): static files holding only
 * the canonical header row, generated from the backend import contract
 * (`python -m app.cli.generate_import_templates`, apps/api) and guarded
 * by tests/unit/test_import_templates.py — the frontend keeps no column
 * list of its own. */
const IMPORT_TEMPLATES = [
  { href: "/templates/participant-import-template.csv", label: "Скачать пример CSV" },
  { href: "/templates/participant-import-template.xlsx", label: "Скачать пример XLSX" },
] as const;

/**
 * Participant Import — «Люди → Импорт» (TH-0118.5, docs/04-ux/
 * import-export-ui.md §3.1, §6; docs/05-api/participant-import-api.md).
 *
 * The canonical workflow upload → parse → validate → preview → approve →
 * apply → report is driven entirely by the backend ImportJob: which step
 * is shown is derived from the job's own `status`, and the job id lives in
 * the URL (`?job=`) so a reload resumes the same job. Every transition is
 * an explicit administrator action — upload never parses, preview never
 * applies, and apply is only offered after an explicit approval plus a
 * confirmation dialog.
 *
 * The frontend performs no validation, duplicate detection or counting of
 * its own: counters and row-level errors/warnings are shown exactly as the
 * backend reports them. The current MVP creates only Person + User +
 * ClubMembership, so this page offers no group, role, instructor or
 * guardian selection.
 */

const IMPORT_STEPS = ["Файл", "Проверка", "Предпросмотр", "Подтверждение", "Результат"] as const;

const TERMINAL_STATUSES: ReadonlySet<ImportJobStatus> = new Set([
  "completed",
  "partially_completed",
  "failed",
  "cancelled",
]);

/** Human-readable labels for the closed import error/warning vocabulary
 * (people-api.md §22 "Import error and warning codes"). Presentation only;
 * an unknown code falls back to the backend's own message. */
const ISSUE_CODE_LABELS: Record<string, string> = {
  required_field_missing: "Не заполнено обязательное поле",
  invalid_email: "Некорректный email",
  invalid_birth_date: "Некорректная дата рождения",
  value_too_long: "Слишком длинное значение",
  invalid_value_type: "Неверный тип значения в ячейке",
  duplicate_exact: "Возможный дубликат — строка будет пропущена",
  import_apply_failed: "Строку не удалось импортировать",
  import_file_unreadable: "Файл не удалось прочитать",
  import_file_malformed: "Файл повреждён или имеет неверную структуру",
  import_header_missing: "В файле нет строки заголовков",
  import_header_unknown_column: "Неизвестная колонка в заголовке",
  import_header_duplicate_column: "Колонка в заголовке повторяется",
  import_header_missing_required_column: "Нет обязательной колонки",
  import_validation_failed: "Файл не прошёл проверку",
};

function stepIndex(job: ImportJob | undefined, confirming: boolean): number {
  if (!job) return 0;
  switch (job.status) {
    case "uploaded":
    case "parsing":
    case "validating":
      return 1;
    case "preview_ready":
      return confirming ? 3 : 2;
    case "approved":
      return 3;
    default:
      return 4;
  }
}

export function ImportPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const importId = searchParams.get("job") ?? undefined;
  const jobQuery = useImportJob(importId);
  const [confirming, setConfirming] = useState(false);

  const startOver = () => {
    setConfirming(false);
    setSearchParams({});
  };

  const job = jobQuery.data;

  return (
    <div>
      <PageHeader
        title="Импорт участников"
        description="Массовое добавление участников клуба из файла CSV или XLSX."
        back={{ to: "/people", label: "Все люди" }}
      />

      <Stepper label="Шаги импорта" steps={IMPORT_STEPS} current={stepIndex(job, confirming)} />

      {!importId ? (
        <UploadStep
          onUploaded={(id) => {
            setConfirming(false);
            setSearchParams({ job: id });
          }}
        />
      ) : null}

      {importId && jobQuery.isLoading ? <Loading label="Загружаем импорт…" /> : null}

      {importId && jobQuery.isError ? (
        <ErrorState
          illustration={
            jobQuery.error.status === 404 ? "404" : jobQuery.error.status === 403 ? "403" : "error"
          }
          title={jobQuery.error.status === 404 ? "Импорт не найден" : "Не удалось загрузить импорт"}
          description={jobQuery.error.message}
          action={
            <Button variant="primary" onClick={startOver}>
              Начать новый импорт
            </Button>
          }
        />
      ) : null}

      {job ? (
        <ImportJobView
          job={job}
          confirming={confirming}
          onConfirming={setConfirming}
          onStartOver={startOver}
        />
      ) : null}
    </div>
  );
}

function ImportJobView({
  job,
  confirming,
  onConfirming,
  onStartOver,
}: {
  job: ImportJob;
  confirming: boolean;
  onConfirming: (value: boolean) => void;
  onStartOver: () => void;
}) {
  switch (job.status) {
    case "uploaded":
      return <CheckStep job={job} onStartOver={onStartOver} />;
    case "parsing":
    case "validating":
      return <Loading label="Проверяем файл…" />;
    case "preview_ready":
      return confirming ? (
        <ApproveStep job={job} onBack={() => onConfirming(false)} />
      ) : (
        <PreviewStep job={job} onNext={() => onConfirming(true)} onStartOver={onStartOver} />
      );
    case "approved":
      return <ApplyStep job={job} />;
    case "applying":
      return <Loading label="Применяем импорт…" />;
    default:
      return TERMINAL_STATUSES.has(job.status) ? (
        <ResultStep job={job} onStartOver={onStartOver} />
      ) : (
        <p className={styles.muted}>Неизвестный статус импорта: {job.status}</p>
      );
  }
}

// --- Step 1: file -----------------------------------------------------------

function uploadErrorMessage(error: ApiError): string {
  if (error.code === "unsupported_import_format") {
    return "Поддерживаются только файлы CSV и XLSX.";
  }
  if (error.status === 403) {
    return "У вас нет прав на импорт участников.";
  }
  return error.message;
}

function UploadStep({ onUploaded }: { onUploaded: (importId: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const upload = useUploadImportFile();

  return (
    <Card className={styles.panel}>
      <h2 className={styles.sectionTitle}>Выберите файл</h2>
      <p className={styles.muted}>
        Загрузка файла ничего не изменяет в данных клуба. После загрузки файл будет проверен, и вы
        увидите предпросмотр результата до применения.
      </p>
      <ul className={styles.hintList}>
        <li>
          Формат: CSV (UTF-8, разделитель — запятая) или XLSX (импортируется только первый лист).
        </li>
        <li>Первая строка — заголовки колонок.</li>
        <li>
          Обязательные колонки: <span className={styles.code}>first_name</span>,{" "}
          <span className={styles.code}>last_name</span>. Дополнительные:{" "}
          <span className={styles.code}>middle_name</span>,{" "}
          <span className={styles.code}>birth_date</span>,{" "}
          <span className={styles.code}>phone</span>, <span className={styles.code}>email</span>,{" "}
          <span className={styles.code}>external_id</span>.
        </li>
        <li>
          Импорт создаёт карточку человека, учётную запись и членство в клубе. Группы, роли и
          представители импортом не назначаются.
        </li>
      </ul>
      <div className={styles.templateActions}>
        <p className={styles.muted}>
          Шаблоны содержат только строку заголовков. Добавьте под ней строки с данными участников
          и загрузите файл ниже.
        </p>
        <div className={styles.actionsStart}>
          {IMPORT_TEMPLATES.map((template) => (
            <a
              key={template.href}
              href={template.href}
              download
              className={`${buttonStyles.button} ${buttonStyles.secondary} ${styles.templateLink}`}
            >
              <Icon id="action.download" size={20} />
              {template.label}
            </a>
          ))}
        </div>
      </div>
      <form
        className={styles.panel}
        onSubmit={(event) => {
          event.preventDefault();
          if (!file) return;
          upload.mutate(file, { onSuccess: (created) => onUploaded(created.import_id) });
        }}
      >
        <label className={styles.optionTitle} htmlFor="import-file">
          Файл для импорта
        </label>
        <input
          id="import-file"
          className={styles.fileInput}
          type="file"
          accept=".csv,.xlsx"
          onChange={(event) => {
            upload.reset();
            setFile(event.target.files?.[0] ?? null);
          }}
        />
        {upload.isError ? (
          <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
            Не удалось загрузить файл: {uploadErrorMessage(upload.error)}
          </p>
        ) : null}
        <div className={styles.actions}>
          <Button
            type="submit"
            variant="primary"
            icon="action.upload"
            disabled={!file || upload.isPending}
          >
            {upload.isPending ? "Загружаем…" : "Загрузить файл"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

// --- Step 2: check (parse + validate) -------------------------------------

function CheckStep({ job, onStartOver }: { job: ImportJob; onStartOver: () => void }) {
  const preview = usePreviewImport();
  const notify = useNotify();

  if (preview.isPending) {
    return <Loading label="Проверяем файл…" />;
  }

  return (
    <Card className={styles.panel}>
      <h2 className={styles.sectionTitle}>Файл загружен</h2>
      <p className={styles.muted}>
        Файл ({job.format.toUpperCase()}) сохранён, но ещё не проверен. Проверка разберёт файл,
        найдёт ошибки и возможные дубликаты. Данные клуба при проверке не изменяются.
      </p>
      <div className={styles.actions}>
        <Button variant="secondary" onClick={onStartOver}>
          Загрузить другой файл
        </Button>
        <Button
          variant="primary"
          onClick={() =>
            preview.mutate(job.import_id, {
              onError: (error) => {
                // 422 `import_validation_failed`: the job is now `failed`
                // and its file-level errors are shown on the result step.
                if (error.code !== "import_validation_failed") notify("error", error.message);
              },
            })
          }
        >
          Проверить файл
        </Button>
      </div>
    </Card>
  );
}

// --- Step 3: preview --------------------------------------------------------

function formatCount(value: number | null): string {
  return value === null ? "—" : String(value);
}

function PreviewStep({
  job,
  onNext,
  onStartOver,
}: {
  job: ImportJob;
  onNext: () => void;
  onStartOver: () => void;
}) {
  const { statistics } = job;
  const warningsQuery = useImportJobIssues(job.import_id, { severity: "warning", page: 1 });
  const warningTotal = warningsQuery.data?.pagination.total;

  return (
    <div className={styles.panel}>
      <Card className={styles.panel}>
        <h2 className={styles.sectionTitle}>Предпросмотр</h2>
        <p className={styles.muted}>
          Это результат проверки файла. Данные клуба ещё не изменены.
        </p>
        <dl className={styles.stats} aria-label="Итоги проверки">
          <div className={styles.stat}>
            <dt>Всего строк</dt>
            <dd>{formatCount(statistics.total_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statSuccess}`}>
            <dt>Без ошибок</dt>
            <dd>{formatCount(statistics.valid_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statError}`}>
            <dt>С ошибками (будут пропущены)</dt>
            <dd>{formatCount(statistics.invalid_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statWarning}`}>
            <dt>Предупреждения о дубликатах</dt>
            <dd>{warningTotal === undefined ? "…" : warningTotal}</dd>
          </div>
        </dl>
        <ApplyConsequences />
      </Card>

      <ImportIssueList
        importId={job.import_id}
        severity="error"
        title="Блокирующие ошибки"
        description="Строки с ошибками не будут импортированы. Исправьте файл и загрузите его заново, если эти строки нужны."
        emptyText="Ошибок нет."
      />
      <ImportIssueList
        importId={job.import_id}
        severity="warning"
        title="Предупреждения и возможные дубликаты"
        description="Строки с предупреждением о дубликате будут пропущены: существующие люди не изменяются и не объединяются автоматически."
        emptyText="Предупреждений нет."
      />

      <div className={styles.actions}>
        <Button variant="secondary" onClick={onStartOver}>
          Загрузить другой файл
        </Button>
        <Button variant="primary" icon="action.forward" onClick={onNext}>
          Перейти к подтверждению
        </Button>
      </div>
    </div>
  );
}

function ApplyConsequences() {
  return (
    <div>
      <h3 className={styles.sectionTitle}>Что произойдёт при применении</h3>
      <ul className={styles.hintList}>
        <li>
          Для каждой строки без ошибок и без предупреждения о дубликате будут созданы карточка
          человека, учётная запись и членство в клубе.
        </li>
        <li>Строки с ошибками и строки-дубликаты будут пропущены.</li>
        <li>Существующие люди не изменяются и не объединяются.</li>
        <li>Группы, роли, инструкторы и представители импортом не назначаются.</li>
        <li>Пароли и приглашения импорт не выдаёт.</li>
      </ul>
    </div>
  );
}

// --- Step 4: approval + apply ----------------------------------------------

function ApproveStep({ job, onBack }: { job: ImportJob; onBack: () => void }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const approve = useApproveImport();
  const notify = useNotify();

  return (
    <Card className={styles.panel}>
      <h2 className={styles.sectionTitle}>Подтверждение</h2>
      <dl className={styles.summary}>
        <dt>Всего строк</dt>
        <dd>{formatCount(job.statistics.total_records)}</dd>
        <dt>Без ошибок</dt>
        <dd>{formatCount(job.statistics.valid_records)}</dd>
        <dt>С ошибками</dt>
        <dd>{formatCount(job.statistics.invalid_records)}</dd>
      </dl>
      <ApplyConsequences />
      <label className={styles.checkbox}>
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        <span>Я проверил(а) предпросмотр и понимаю, какие записи будут созданы.</span>
      </label>
      <div className={styles.actions}>
        <Button variant="secondary" icon="action.back" onClick={onBack} disabled={approve.isPending}>
          К предпросмотру
        </Button>
        <Button
          variant="primary"
          icon="action.confirm"
          disabled={!acknowledged || approve.isPending}
          onClick={() =>
            approve.mutate(job.import_id, {
              onError: (error) => notify("error", error.message),
            })
          }
        >
          {approve.isPending ? "Одобряем…" : "Одобрить импорт"}
        </Button>
      </div>
    </Card>
  );
}

function ApplyStep({ job }: { job: ImportJob }) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const apply = useApplyImport();
  const notify = useNotify();

  if (apply.isPending) {
    return <Loading label="Применяем импорт…" />;
  }

  return (
    <Card className={styles.panel}>
      <h2 className={styles.sectionTitle}>Импорт одобрен</h2>
      <p className={styles.notice}>
        Изменения ещё не применены. После применения новые участники появятся в клубе — это
        действие нельзя отменить.
      </p>
      <dl className={styles.summary}>
        <dt>Всего строк</dt>
        <dd>{formatCount(job.statistics.total_records)}</dd>
        <dt>Без ошибок</dt>
        <dd>{formatCount(job.statistics.valid_records)}</dd>
      </dl>
      <div className={styles.actions}>
        <Button variant="primary" icon="action.confirm" onClick={() => setConfirmOpen(true)}>
          Применить импорт
        </Button>
      </div>
      <ConfirmDialog
        open={confirmOpen}
        title="Применить импорт?"
        description="Будут созданы карточки людей, учётные записи и членства в клубе для строк без ошибок и дубликатов. Действие необратимо."
        confirmLabel="Применить"
        destructive
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          apply.mutate(job.import_id, {
            onSuccess: (result) => {
              if (result.status === "completed") notify("success", "Импорт завершён");
            },
            onError: (error) => notify("error", error.message),
          });
        }}
      />
    </Card>
  );
}

// --- Step 5: result / report ----------------------------------------------

const RESULT_PRESENTATION: Record<string, { icon: StatusIconId; label: string }> = {
  completed: { icon: "status.success", label: "Импорт завершён" },
  partially_completed: { icon: "status.warning", label: "Импорт завершён частично" },
  failed: { icon: "status.error", label: "Импорт не выполнен" },
  cancelled: { icon: "status.archived", label: "Импорт отменён" },
};

function ResultStep({ job, onStartOver }: { job: ImportJob; onStartOver: () => void }) {
  const presentation = RESULT_PRESENTATION[job.status] ?? {
    icon: "status.info" as const,
    label: job.status,
  };
  const { statistics } = job;

  return (
    <div className={styles.panel}>
      <Card className={styles.panel}>
        <h2 className={styles.sectionTitle}>Результат</h2>
        <div>
          <StatusBadge status={presentation.icon} label={presentation.label} />
        </div>
        <dl className={styles.stats} aria-label="Итоги импорта">
          <div className={styles.stat}>
            <dt>Всего строк</dt>
            <dd>{formatCount(statistics.total_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statSuccess}`}>
            <dt>Создано</dt>
            <dd>{formatCount(statistics.created_records)}</dd>
          </div>
          <div className={styles.stat}>
            <dt>Обновлено</dt>
            <dd>{formatCount(statistics.updated_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statWarning}`}>
            <dt>Пропущено</dt>
            <dd>{formatCount(statistics.skipped_records)}</dd>
          </div>
          <div className={`${styles.stat} ${styles.statError}`}>
            <dt>Ошибок</dt>
            <dd>{statistics.error_count}</dd>
          </div>
        </dl>
        {job.status === "completed" ? (
          <p className={styles.muted}>
            Исходный файл удалён после успешного импорта. Итоги и журнал ошибок сохранены.
          </p>
        ) : null}
        {job.status === "failed" || job.status === "partially_completed" ? (
          <p className={styles.muted}>
            Исходный файл сохранён, чтобы можно было разобраться с результатом. Для повтора
            исправьте файл и начните новый импорт.
          </p>
        ) : null}
      </Card>

      <ImportIssueList
        importId={job.import_id}
        severity="error"
        title="Ошибки"
        emptyText="Ошибок нет."
      />
      <ImportIssueList
        importId={job.import_id}
        severity="warning"
        title="Предупреждения и пропущенные дубликаты"
        emptyText="Предупреждений нет."
      />

      <div className={styles.actions}>
        <Link to="/people">
          <Button variant="secondary">К списку людей</Button>
        </Link>
        <Button variant="primary" icon="action.upload" onClick={onStartOver}>
          Новый импорт
        </Button>
      </div>
    </div>
  );
}

// --- Row-level report ------------------------------------------------------

function issueLocation(issue: ImportJobIssue): string {
  const parts: string[] = [];
  parts.push(issue.row_number === null ? "Весь файл" : `Строка ${issue.row_number}`);
  if (issue.field) parts.push(`колонка ${issue.field}`);
  return parts.join(" · ");
}

function ImportIssueList({
  importId,
  severity,
  title,
  description,
  emptyText,
}: {
  importId: string;
  severity: ImportIssueSeverity;
  title: string;
  description?: string;
  emptyText: string;
}) {
  const [page, setPage] = useState(1);
  const issuesQuery = useImportJobIssues(importId, { severity, page });
  const headingId = `import-issues-${severity}`;

  return (
    <Card className={styles.panel}>
      <section className={styles.panel} aria-labelledby={headingId}>
        <h3 className={styles.sectionTitle} id={headingId}>
          {title}
          {issuesQuery.data ? ` (${issuesQuery.data.pagination.total})` : ""}
        </h3>
        {description ? <p className={styles.muted}>{description}</p> : null}
        {issuesQuery.isLoading ? <Loading label="Загружаем…" /> : null}
        {issuesQuery.isError ? (
          <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
            Не удалось загрузить список: {issuesQuery.error.message}
          </p>
        ) : null}
        {issuesQuery.data && issuesQuery.data.items.length === 0 ? (
          <p className={styles.muted}>{emptyText}</p>
        ) : null}
        {issuesQuery.data && issuesQuery.data.items.length > 0 ? (
          <>
            <ul className={styles.issueList}>
              {issuesQuery.data.items.map((issue) => (
                <li key={issue.id} className={styles.issue}>
                  <span className={styles.issueMeta}>{issueLocation(issue)}</span>
                  <span>{ISSUE_CODE_LABELS[issue.code] ?? issue.message}</span>
                  {issue.code === "duplicate_exact" ? (
                    issue.matched_person_id ? (
                      <Link to={`/people/${issue.matched_person_id}`}>
                        Открыть совпавшего человека
                      </Link>
                    ) : (
                      <span className={styles.issueMeta}>Совпадает с другой строкой файла</span>
                    )
                  ) : null}
                </li>
              ))}
            </ul>
            <Pagination
              page={issuesQuery.data.pagination.page}
              pages={issuesQuery.data.pagination.pages}
              total={issuesQuery.data.pagination.total}
              onPageChange={setPage}
            />
          </>
        ) : null}
      </section>
    </Card>
  );
}
