import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useAddInventoryIssueLines,
  useCancelInventoryIssue,
  useInventoryInstance,
  useInventoryIssue,
  useInventoryIssueLines,
  useInventoryIssueMovements,
  useRemoveInventoryIssueLine,
  useReportLostInventoryInstance,
  useReturnInventoryIssue,
  useUpdateInventoryIssue,
  type InventoryIssueDetail,
  type InventoryIssueHeaderFields,
  type InventoryIssueLine,
  type IssueLineStatusFilter,
  type RecipientType,
} from "../api/inventory";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { FilterSelect, type FilterOption } from "../components/ui/FilterSelect";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { useNotify } from "../components/ui/notificationContext";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import {
  accountingModeLabel,
  formatCalendarDate,
  formatMovementDate,
  formatQuantity,
  parseQuantity,
  recipientTypeLabel,
} from "../domain/inventoryFormat";
import { mutationErrorMessage } from "../domain/inventoryErrors";
import { unitName, useInventoryLookups, type InventoryLookups } from "../hooks/useInventoryLookups";
import { FormDialog, SelectField, TextAreaField } from "./InventoryForms";
import {
  EventField,
  EventName,
  IssueStatusBadges,
  LinesEditor,
  RecipientFields,
  RecipientName,
} from "./InventoryIssues";
import { activeLocationOptions, draftLinesToInput, newDraftLine, type DraftLine } from "./inventoryHelpers";
import { InstanceLink, InventoryQueryError, MetaList, MovementList } from "./InventoryShared";
import styles from "./Inventory.module.css";

/**
 * One issue document (docs/04-domain/inventory.md §14): header, lines
 * with issued / returned / outstanding as computed by the backend, and
 * the document's movement history. Every operation is one documented
 * request; the page then refetches. Operations are offered only while the
 * backend reports the document as `issued` with something outstanding
 * (`has_outstanding`) — the backend still decides each request.
 */

const LINE_STATUS_OPTIONS: FilterOption[] = [
  { value: "active", label: "Активные строки" },
  { value: "removed", label: "Удалённые строки" },
  { value: "all", label: "Все строки" },
];

type Operation =
  | { kind: "return"; lineId?: string }
  | { kind: "return-all" }
  | { kind: "cancel" }
  | { kind: "edit" }
  | { kind: "add-lines" }
  | { kind: "remove-line"; line: InventoryIssueLine }
  | { kind: "lost"; instanceId: string };

export function InventoryIssuePage() {
  const { issueId } = useParams<{ issueId: string }>();
  const issueQuery = useInventoryIssue(issueId);
  const { lookups, isLoading, error, refetch } = useInventoryLookups();
  const [operation, setOperation] = useState<Operation | null>(null);
  const back = { to: "/inventory?tab=issues", label: "Выдачи" };

  if (issueQuery.isLoading || isLoading) {
    return (
      <div className={styles.page}>
        <PageHeader title="Выдача" back={back} />
        <Loading label="Загружаем выдачу…" />
      </div>
    );
  }
  const failure = issueQuery.error ?? error;
  if (failure) {
    return (
      <div className={styles.page}>
        <PageHeader title="Выдача" back={back} />
        <InventoryQueryError
          error={failure}
          title="Не удалось загрузить выдачу"
          pathParam="issue_id"
          onRetry={() => {
            void issueQuery.refetch();
            refetch();
          }}
        />
      </div>
    );
  }
  const issue = issueQuery.data;
  if (!issue) return null;
  const operable = issue.status === "issued" && issue.has_outstanding;
  const close = () => setOperation(null);

  return (
    <div className={styles.page}>
      <PageHeader
        title={`Выдача от ${formatMovementDate(issue.created_at)}`}
        back={back}
        titleExtra={<IssueStatusBadges issue={issue} />}
      />
      <Card>
        <div className={styles.rowHeader}>
          <h2 className={styles.rowTitle}>
            <RecipientName type={issue.recipient_type} id={issue.recipient_id} />
          </h2>
        </div>
        <MetaList
          entries={[
            { label: "Тип получателя", value: recipientTypeLabel(issue.recipient_type) },
            {
              label: "Плановый возврат",
              value: issue.planned_return_date ? formatCalendarDate(issue.planned_return_date) : "—",
            },
            { label: "Комментарий", value: issue.comment ?? "—" },
            ...(issue.cancelled_at ? [{ label: "Отменена", value: formatMovementDate(issue.cancelled_at) }] : []),
          ]}
        />
        <p className={styles.muted}>
          Мероприятие: {issue.event_id ? <EventName id={issue.event_id} /> : "не указано"}
        </p>
        {operable ? (
          <div className={styles.cardActions} aria-label="Действия с выдачей" role="group">
            <Button variant="primary" onClick={() => setOperation({ kind: "return" })}>
              Вернуть
            </Button>
            <Button variant="secondary" onClick={() => setOperation({ kind: "return-all" })}>
              Вернуть всё
            </Button>
            <Button variant="secondary" icon="action.edit" onClick={() => setOperation({ kind: "edit" })}>
              Изменить
            </Button>
            <Button variant="secondary" icon="action.add" onClick={() => setOperation({ kind: "add-lines" })}>
              Добавить позиции
            </Button>
            <Button variant="destructive" onClick={() => setOperation({ kind: "cancel" })}>
              Отменить выдачу
            </Button>
          </div>
        ) : (
          <p className={styles.muted}>
            {issue.status === "cancelled"
              ? "Выдача отменена и больше не изменяется."
              : "Всё имущество возвращено — выдача больше не изменяется."}
          </p>
        )}
      </Card>

      <IssueLinesSection issue={issue} lookups={lookups} operable={operable} onOperation={setOperation} />
      <IssueHistorySection issue={issue} lookups={lookups} />

      {operation?.kind === "return" ? (
        <ReturnDialog issue={issue} lookups={lookups} lineId={operation.lineId} onClose={close} />
      ) : null}
      {operation?.kind === "return-all" ? <ReturnAllDialog issue={issue} lookups={lookups} onClose={close} /> : null}
      {operation?.kind === "cancel" ? <CancelIssueDialog issue={issue} lookups={lookups} onClose={close} /> : null}
      {operation?.kind === "edit" ? <EditIssueDialog issue={issue} onClose={close} /> : null}
      {operation?.kind === "add-lines" ? <AddLinesDialog issue={issue} lookups={lookups} onClose={close} /> : null}
      {operation?.kind === "remove-line" ? (
        <RemoveLineDialog issue={issue} line={operation.line} lookups={lookups} onClose={close} />
      ) : null}
      {operation?.kind === "lost" ? (
        <LostInstanceDialog issue={issue} instanceId={operation.instanceId} lookups={lookups} onClose={close} />
      ) : null}
    </div>
  );
}

// --- lines -----------------------------------------------------------------------

function IssueLinesSection({
  issue,
  lookups,
  operable,
  onOperation,
}: {
  issue: InventoryIssueDetail;
  lookups: InventoryLookups;
  operable: boolean;
  onOperation: (operation: Operation) => void;
}) {
  const [status, setStatus] = useState<IssueLineStatusFilter>("active");
  const query = useInventoryIssueLines(issue.id, status);

  return (
    <section className={styles.section} aria-labelledby="inventory-issue-lines">
      <div className={styles.sectionHeader}>
        <h2 id="inventory-issue-lines" className={styles.sectionTitle}>
          Имущество
        </h2>
      </div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Строки"
          value={status}
          options={LINE_STATUS_OPTIONS}
          onChange={(value) => setStatus(value as IssueLineStatusFilter)}
        />
      </div>
      {query.isLoading ? <Loading label="Загружаем строки…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить строки"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState
          illustration={status === "active" ? "empty-inventory" : "no-results"}
          title={status === "removed" ? "Удалённых строк нет" : "Строк нет"}
        />
      ) : null}
      {query.isSuccess && query.data.length > 0 ? (
        <ul className={styles.list} aria-label="Строки выдачи">
          {query.data.map((line) => (
            <li key={line.id}>
              <IssueLineCard line={line} lookups={lookups} operable={operable} onOperation={onOperation} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function IssueLineCard({
  line,
  lookups,
  operable,
  onOperation,
}: {
  line: InventoryIssueLine;
  lookups: InventoryLookups;
  operable: boolean;
  onOperation: (operation: Operation) => void;
}) {
  const item = lookups.items.get(line.item_id);
  const removed = line.removed_at !== null;
  const unit = line.accounting_mode === "quantity" ? unitName(item, lookups) : "шт";
  const lineOperable = operable && !removed;

  return (
    <Card className={removed ? styles.removedLine : undefined}>
      <div className={styles.rowHeader}>
        <h3 className={styles.rowTitle}>{item?.name ?? "Позиция"}</h3>
        {removed ? (
          <StatusBadge status="status.archived" label={`Удалена ${formatMovementDate(line.removed_at ?? "")}`} />
        ) : null}
      </div>
      <MetaList
        entries={[
          { label: "Режим учёта", value: accountingModeLabel(line.accounting_mode) },
          { label: "Выдано", value: formatQuantity(line.issued_quantity, unit) },
          { label: "Возвращено", value: formatQuantity(line.returned_quantity, unit) },
          { label: "Числится выданным", value: formatQuantity(line.outstanding_quantity, unit) },
        ]}
      />
      {line.outstanding_instance_ids.length > 0 ? (
        <ul className={styles.checkList} aria-label="Выданные экземпляры">
          {line.outstanding_instance_ids.map((instanceId) => (
            <li key={instanceId} className={styles.rowHeader}>
              <InstanceLink instanceId={instanceId} />
              {lineOperable ? (
                <Button variant="secondary" onClick={() => onOperation({ kind: "lost", instanceId })}>
                  Утерян
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {lineOperable ? (
        <div className={styles.cardActions}>
          {line.outstanding_quantity > 0 ? (
            <Button variant="secondary" onClick={() => onOperation({ kind: "return", lineId: line.id })}>
              Вернуть
            </Button>
          ) : (
            // Offered only when the backend reports nothing outstanding on
            // the line; the backend still decides the removal itself.
            <Button variant="secondary" icon="action.delete" onClick={() => onOperation({ kind: "remove-line", line })}>
              Удалить строку
            </Button>
          )}
        </div>
      ) : null}
    </Card>
  );
}

function IssueHistorySection({ issue, lookups }: { issue: InventoryIssueDetail; lookups: InventoryLookups }) {
  const [page, setPage] = useState(1);
  const query = useInventoryIssueMovements(issue.id, page);

  return (
    <section className={styles.section} aria-labelledby="inventory-issue-history">
      <h2 id="inventory-issue-history" className={styles.sectionTitle}>
        История выдачи и возвратов
      </h2>
      {query.isLoading ? <Loading label="Загружаем историю…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить историю"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState illustration="empty-inventory" title="Движений пока нет" />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <MovementList movements={query.data.items} lookups={lookups} showItem />
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}
    </section>
  );
}

// --- operations ------------------------------------------------------------------

function useOperationResult(onClose: () => void) {
  const notify = useNotify();
  const [error, setError] = useState<string | null>(null);
  return {
    error,
    setError,
    handlers: (message: string) => ({
      onSuccess: () => {
        notify("success", message);
        onClose();
      },
      onError: (failure: unknown) => setError(mutationErrorMessage(failure)),
    }),
  };
}

function DestinationField({
  value,
  onChange,
  lookups,
}: {
  value: string;
  onChange: (value: string) => void;
  lookups: InventoryLookups;
}) {
  return (
    <SelectField
      label="Место хранения для возврата"
      value={value}
      placeholder="Выберите место"
      options={activeLocationOptions(lookups)}
      onChange={onChange}
    />
  );
}

/** Partial or full return of chosen lines/instances into one chosen
 * location. Quantities are what the Administrator types; the backend
 * rejects more than is outstanding. */
function ReturnDialog({
  issue,
  lookups,
  lineId,
  onClose,
}: {
  issue: InventoryIssueDetail;
  lookups: InventoryLookups;
  lineId?: string;
  onClose: () => void;
}) {
  const lines = issue.lines.filter(
    (line) => (!lineId || line.id === lineId) && (line.outstanding_quantity > 0 || line.outstanding_instance_ids.length > 0),
  );
  const [locationId, setLocationId] = useState("");
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [instanceIds, setInstanceIds] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const returnItems = useReturnInventoryIssue();
  const { error, setError, handlers } = useOperationResult(onClose);

  const quantityEntries = lines
    .filter((line) => line.accounting_mode === "quantity" && quantities[line.id]?.trim())
    .map((line) => ({ line_id: line.id, quantity: parseQuantity(quantities[line.id] ?? "") }));
  const quantitiesValid = quantityEntries.every((entry) => entry.quantity !== null);
  const hasSomething = quantityEntries.length > 0 || instanceIds.length > 0;

  return (
    <FormDialog
      title="Вернуть"
      description="Укажите, что возвращается, и место хранения, куда оно поступает."
      submitLabel="Вернуть"
      pending={returnItems.isPending}
      error={error}
      canSubmit={Boolean(locationId) && quantitiesValid && hasSomething}
      onSubmit={() => {
        setError(null);
        returnItems.mutate(
          {
            id: issue.id,
            storage_location_id: locationId,
            quantities: quantityEntries.map((entry) => ({ line_id: entry.line_id, quantity: entry.quantity ?? 0 })),
            instance_ids: instanceIds,
            comment: comment.trim() || null,
          },
          handlers("Возврат оформлен"),
        );
      }}
      onClose={onClose}
    >
      <DestinationField value={locationId} onChange={setLocationId} lookups={lookups} />
      {lines.map((line) => {
        const item = lookups.items.get(line.item_id);
        if (line.accounting_mode === "quantity") {
          const unit = unitName(item, lookups);
          return (
            <Input
              key={line.id}
              label={`${item?.name ?? "Позиция"}, ${unit || "ед."}`}
              value={quantities[line.id] ?? ""}
              inputMode="numeric"
              onChange={(event) => setQuantities({ ...quantities, [line.id]: event.target.value })}
              hint={`Числится выданным: ${formatQuantity(line.outstanding_quantity, unit)}`}
            />
          );
        }
        return (
          <fieldset key={line.id} className={styles.fieldset}>
            <legend className={styles.legend}>{item?.name ?? "Позиция"}</legend>
            <ul className={styles.checkList}>
              {line.outstanding_instance_ids.map((instanceId) => (
                <li key={instanceId}>
                  <label className={styles.checkItem}>
                    <input
                      type="checkbox"
                      checked={instanceIds.includes(instanceId)}
                      onChange={(event) =>
                        setInstanceIds(
                          event.target.checked
                            ? [...instanceIds, instanceId]
                            : instanceIds.filter((id) => id !== instanceId),
                        )
                      }
                    />
                    <InstanceNumber instanceId={instanceId} />
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        );
      })}
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

/** Plain Inventory ID inside a checkbox label (a link there would
 * navigate away on click). */
function InstanceNumber({ instanceId }: { instanceId: string }) {
  const query = useInventoryInstance(instanceId);
  return <span>{query.data?.inventory_number ?? "Экземпляр"}</span>;
}

/** «Вернуть всё»: one return request carrying everything the backend
 * currently reports as outstanding on the document; the backend
 * performs it atomically or rejects it. */
function ReturnAllDialog({
  issue,
  lookups,
  onClose,
}: {
  issue: InventoryIssueDetail;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState("");
  const [comment, setComment] = useState("");
  const returnItems = useReturnInventoryIssue();
  const { error, setError, handlers } = useOperationResult(onClose);

  return (
    <FormDialog
      title="Вернуть всё"
      description="Всё, что числится выданным по этой выдаче, поступит в выбранное место хранения."
      submitLabel="Вернуть всё"
      pending={returnItems.isPending}
      error={error}
      canSubmit={Boolean(locationId)}
      onSubmit={() => {
        setError(null);
        returnItems.mutate(
          {
            id: issue.id,
            storage_location_id: locationId,
            quantities: issue.lines
              .filter((line) => line.accounting_mode === "quantity" && line.outstanding_quantity > 0)
              .map((line) => ({ line_id: line.id, quantity: line.outstanding_quantity })),
            instance_ids: issue.lines.flatMap((line) => line.outstanding_instance_ids),
            comment: comment.trim() || null,
          },
          handlers("Всё имущество возвращено"),
        );
      }}
      onClose={onClose}
    >
      <DestinationField value={locationId} onChange={setLocationId} lookups={lookups} />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

/** Cancellation: the backend returns everything outstanding into the
 * chosen location and marks the document `cancelled` (§14 п.13). */
function CancelIssueDialog({
  issue,
  lookups,
  onClose,
}: {
  issue: InventoryIssueDetail;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState("");
  const cancel = useCancelInventoryIssue();
  const { error, setError, handlers } = useOperationResult(onClose);

  return (
    <FormDialog
      title="Отменить выдачу?"
      description="Всё невозвращённое имущество вернётся в выбранное место, выдача станет отменённой и больше не изменится."
      submitLabel="Отменить выдачу"
      destructive
      pending={cancel.isPending}
      error={error}
      canSubmit={Boolean(locationId)}
      onSubmit={() => {
        setError(null);
        cancel.mutate({ id: issue.id, storage_location_id: locationId }, handlers("Выдача отменена"));
      }}
      onClose={onClose}
    >
      <DestinationField value={locationId} onChange={setLocationId} lookups={lookups} />
    </FormDialog>
  );
}

/** Header edit (§14 п.14): recipient, Event, planned return date and
 * comment; only changed fields are sent, `null` clears one. */
function EditIssueDialog({ issue, onClose }: { issue: InventoryIssueDetail; onClose: () => void }) {
  const [recipientType, setRecipientType] = useState<RecipientType>(issue.recipient_type);
  const [recipientId, setRecipientId] = useState(issue.recipient_id);
  const [eventId, setEventId] = useState(issue.event_id ?? "");
  const [plannedReturn, setPlannedReturn] = useState(issue.planned_return_date ?? "");
  const [comment, setComment] = useState(issue.comment ?? "");
  const update = useUpdateInventoryIssue();
  const { error, setError, handlers } = useOperationResult(onClose);

  function submit() {
    const fields: InventoryIssueHeaderFields = {};
    if (recipientType !== issue.recipient_type || recipientId !== issue.recipient_id) {
      fields.recipient_type = recipientType;
      fields.recipient_id = recipientId;
    }
    if ((eventId || null) !== issue.event_id) fields.event_id = eventId || null;
    if ((plannedReturn || null) !== issue.planned_return_date) fields.planned_return_date = plannedReturn || null;
    if ((comment.trim() || null) !== issue.comment) fields.comment = comment.trim() || null;
    if (Object.keys(fields).length === 0) {
      onClose();
      return;
    }
    setError(null);
    update.mutate({ id: issue.id, fields }, handlers("Выдача сохранена"));
  }

  return (
    <FormDialog
      title="Изменить выдачу"
      description="Количество уменьшается только возвратом."
      submitLabel="Сохранить"
      pending={update.isPending}
      error={error}
      canSubmit={Boolean(recipientId)}
      onSubmit={submit}
      onClose={onClose}
    >
      <RecipientFields
        type={recipientType}
        id={recipientId}
        onChange={(type, id) => {
          setRecipientType(type);
          setRecipientId(id);
        }}
      />
      <EventField value={eventId} onChange={setEventId} />
      {eventId ? (
        <div className={styles.actions}>
          <Button type="button" variant="secondary" onClick={() => setEventId("")}>
            Убрать мероприятие
          </Button>
        </div>
      ) : null}
      <Input
        label="Плановая дата возврата"
        type="date"
        value={plannedReturn}
        onChange={(event) => setPlannedReturn(event.target.value)}
      />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

/** Adds lines to the document; an item already on an active line gets
 * an additional issue movement on that line (backend, §14 п.14). */
function AddLinesDialog({
  issue,
  lookups,
  onClose,
}: {
  issue: InventoryIssueDetail;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [lines, setLines] = useState<DraftLine[]>(() => [newDraftLine()]);
  const add = useAddInventoryIssueLines();
  const { error, setError, handlers } = useOperationResult(onClose);
  const lineInput = draftLinesToInput(lines, lookups);

  return (
    <FormDialog
      title="Добавить позиции"
      description="Имущество выдаётся сразу при сохранении."
      submitLabel="Выдать"
      pending={add.isPending}
      error={error}
      canSubmit={Boolean(lineInput)}
      onSubmit={() => {
        if (!lineInput) return;
        setError(null);
        add.mutate({ id: issue.id, lines: lineInput }, handlers("Позиции выданы"));
      }}
      onClose={onClose}
    >
      <LinesEditor lines={lines} onChange={setLines} lookups={lookups} />
    </FormDialog>
  );
}

function RemoveLineDialog({
  issue,
  line,
  lookups,
  onClose,
}: {
  issue: InventoryIssueDetail;
  line: InventoryIssueLine;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const remove = useRemoveInventoryIssueLine();
  const { error, setError, handlers } = useOperationResult(onClose);
  const name = lookups.items.get(line.item_id)?.name ?? "Позиция";

  return (
    <FormDialog
      title="Удалить строку?"
      description={`«${name}» уйдёт из рабочего состава выдачи. Строка и её история движений сохранятся.`}
      submitLabel="Удалить строку"
      destructive
      pending={remove.isPending}
      error={error}
      onSubmit={() => {
        setError(null);
        remove.mutate({ issueId: issue.id, lineId: line.id }, handlers("Строка удалена"));
      }}
      onClose={onClose}
    />
  );
}

/** «Утерян»: one backend operation — return into the chosen location and
 * immediate write-off with the loss reason (§14 п.17). */
function LostInstanceDialog({
  issue,
  instanceId,
  lookups,
  onClose,
}: {
  issue: InventoryIssueDetail;
  instanceId: string;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState("");
  const [reason, setReason] = useState("");
  const [comment, setComment] = useState("");
  const lost = useReportLostInventoryInstance();
  const { error, setError, handlers } = useOperationResult(onClose);

  return (
    <FormDialog
      title="Экземпляр утерян"
      description="Экземпляр будет возвращён в выбранное место и сразу списан с указанной причиной."
      submitLabel="Оформить утерю"
      destructive
      pending={lost.isPending}
      error={error}
      canSubmit={Boolean(locationId && reason.trim())}
      onSubmit={() => {
        setError(null);
        lost.mutate(
          {
            id: issue.id,
            instance_id: instanceId,
            storage_location_id: locationId,
            reason: reason.trim(),
            comment: comment.trim() || null,
          },
          handlers("Утеря оформлена"),
        );
      }}
      onClose={onClose}
    >
      <p className={styles.muted}>
        Экземпляр: <InstanceLink instanceId={instanceId} />
      </p>
      <SelectField
        label="Место хранения"
        value={locationId}
        placeholder="Выберите место"
        options={activeLocationOptions(lookups)}
        onChange={setLocationId}
      />
      <TextAreaField label="Причина утери" value={reason} onChange={setReason} hint="Обязательно. Сохранится как причина списания." />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} hint="Необязательно." />
    </FormDialog>
  );
}
