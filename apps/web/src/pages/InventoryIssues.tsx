import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { currentClubId, useCurrentUser } from "../api/auth";
import { useEvent, useEventSearch } from "../api/events";
import { useAllGroups, useGroup } from "../api/groups";
import {
  useCreateInventoryIssue,
  useInventoryIssues,
  useInventoryItemInstances,
  type InventoryIssue,
  type IssueStatus,
  type RecipientType,
} from "../api/inventory";
import { personFullName, usePerson, usePersons } from "../api/people";
import { useAllUsersWithRole, userFullName, useUsers } from "../api/users";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { FilterSelect, type FilterOption } from "../components/ui/FilterSelect";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { useNotify } from "../components/ui/notificationContext";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  RECIPIENT_TYPES,
  accountingModeLabel,
  formatCalendarDate,
  formatMovementDate,
  issueStatusLabel,
  outstandingLabel,
  recipientTypeLabel,
} from "../domain/inventoryFormat";
import { mutationErrorMessage } from "../domain/inventoryErrors";
import { unitName, type InventoryLookups } from "../hooks/useInventoryLookups";
import { FormDialog, SelectField, TextAreaField } from "./InventoryForms";
import { draftLinesToInput, issueStatusIcon, newDraftLine, type DraftLine, type Option } from "./inventoryHelpers";
import { InventoryQueryError, MetaList } from "./InventoryShared";
import styles from "./Inventory.module.css";

/**
 * Issue / return (Slice 4, docs/04-domain/inventory.md §14): the list of
 * issue documents and the «Выдать» dialog. Creating a document is the
 * issue itself — there is no draft state.
 */

const STATUS_OPTIONS: FilterOption[] = [
  { value: "", label: "Все" },
  { value: "issued", label: issueStatusLabel("issued") },
  { value: "cancelled", label: issueStatusLabel("cancelled") },
];

const RECIPIENT_TYPE_FILTER: FilterOption[] = [
  { value: "", label: "Все получатели" },
  ...RECIPIENT_TYPES.map((type) => ({ value: type, label: recipientTypeLabel(type) })),
];

export function IssueStatusBadges({ issue }: { issue: InventoryIssue }) {
  return (
    <span className={styles.actions}>
      <StatusBadge status={issueStatusIcon(issue)} label={issueStatusLabel(issue.status)} />
      {issue.status === "issued" ? (
        <StatusBadge
          status={issue.has_outstanding ? "status.warning" : "status.success"}
          label={outstandingLabel(issue.has_outstanding)}
        />
      ) : null}
    </span>
  );
}

export function IssuesTab({ lookups }: { lookups: InventoryLookups }) {
  const [status, setStatus] = useState<IssueStatus | "">("");
  const [recipientType, setRecipientType] = useState<RecipientType | "">("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const query = useInventoryIssues({ status, recipientType, page });
  const filtered = Boolean(status || recipientType);

  const createButton = (
    <Button variant="primary" icon="action.add" onClick={() => setCreateOpen(true)}>
      Выдать
    </Button>
  );

  return (
    <div>
      <div className={styles.sectionHeader}>
        <p className={styles.muted}>Документы выдачи имущества участникам, инструкторам и группам.</p>
        {createButton}
      </div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Статус"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => {
            setStatus(value as IssueStatus | "");
            setPage(1);
          }}
        />
        <FilterSelect
          label="Тип получателя"
          value={recipientType}
          options={RECIPIENT_TYPE_FILTER}
          onChange={(value) => {
            setRecipientType(value as RecipientType | "");
            setPage(1);
          }}
        />
      </div>
      {query.isLoading ? <Loading label="Загружаем выдачи…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить выдачи"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState
          illustration={filtered ? "no-results" : "empty-inventory"}
          title={filtered ? "Ничего не найдено" : "Выдач пока нет"}
          description={filtered ? "Попробуйте изменить фильтры." : "Оформите первую выдачу имущества."}
          action={filtered ? undefined : createButton}
        />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <ul className={styles.list} aria-label="Выдачи">
            {query.data.items.map((issue) => (
              <li key={issue.id}>
                <Card>
                  <div className={styles.rowHeader}>
                    <h3 className={styles.rowTitle}>
                      <Link to={`/inventory/issues/${issue.id}`} className={styles.rowTitleLink}>
                        <RecipientName type={issue.recipient_type} id={issue.recipient_id} />
                      </Link>
                    </h3>
                    <IssueStatusBadges issue={issue} />
                  </div>
                  <MetaList
                    entries={[
                      { label: "Получатель", value: recipientTypeLabel(issue.recipient_type) },
                      { label: "Выдано", value: formatMovementDate(issue.created_at) },
                      {
                        label: "Плановый возврат",
                        value: issue.planned_return_date ? formatCalendarDate(issue.planned_return_date) : "—",
                      },
                      ...(issue.comment ? [{ label: "Комментарий", value: issue.comment }] : []),
                    ]}
                  />
                  {issue.event_id ? (
                    <p className={styles.muted}>
                      Мероприятие: <EventName id={issue.event_id} />
                    </p>
                  ) : null}
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
      {createOpen ? <IssueCreateDialog lookups={lookups} onClose={() => setCreateOpen(false)} /> : null}
    </div>
  );
}

// --- names of linked records -----------------------------------------------------

function MemberName({ id }: { id: string }) {
  const query = usePerson(id);
  return <>{query.data ? personFullName(query.data) : "Участник"}</>;
}

function InstructorName({ id }: { id: string }) {
  const query = useAllUsersWithRole("instructor");
  const user = query.data?.find((entry) => entry.id === id);
  return <>{user ? userFullName(user) : "Инструктор"}</>;
}

function GroupName({ id }: { id: string }) {
  const query = useGroup(id);
  return <>{query.data?.name ?? "Группа"}</>;
}

export function RecipientName({ type, id }: { type: RecipientType; id: string }) {
  switch (type) {
    case "member":
      return <MemberName id={id} />;
    case "instructor":
      return <InstructorName id={id} />;
    case "group":
      return <GroupName id={id} />;
  }
}

export function EventName({ id }: { id: string }) {
  const query = useEvent(id);
  return <>{query.data?.title ?? "Мероприятие"}</>;
}

// --- recipient / event pickers ---------------------------------------------------

/** A search box plus a select of the backend's matches. The current
 * value stays selectable even when a new search no longer returns it. */
function SearchPicker({
  label,
  search,
  onSearch,
  value,
  currentLabel,
  options,
  onChange,
  placeholder,
  loading,
}: {
  label: string;
  search: string;
  onSearch: (value: string) => void;
  value: string;
  currentLabel?: string;
  options: Option[];
  onChange: (value: string) => void;
  placeholder: string;
  loading: boolean;
}) {
  const merged =
    value && currentLabel && !options.some((option) => option.value === value)
      ? [{ value, label: currentLabel }, ...options]
      : options;
  return (
    <div className={styles.form}>
      <Input
        label={`Поиск: ${label.toLowerCase()}`}
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Начните вводить имя или название"
      />
      <SelectField
        label={label}
        value={value}
        placeholder={loading ? "Загружаем…" : placeholder}
        options={merged}
        onChange={onChange}
      />
    </div>
  );
}

/** Members eligible as recipients: the backend's own `club_id`
 * eligibility filter on `GET /persons` (an active ClubMembership in the
 * Club — the Member recipient rule, inventory.md §25). Without a known
 * Club the directory is never queried unfiltered. */
function MemberPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 300);
  const me = useCurrentUser();
  const clubId = currentClubId(me.data);
  const query = usePersons({ page: 1, search: debounced, clubId: clubId ?? undefined, enabled: Boolean(clubId) });
  const current = usePerson(value || undefined);
  if (me.isSuccess && !clubId) {
    return (
      <SelectField
        label="Участник"
        value=""
        options={[]}
        onChange={onChange}
        placeholder="Недоступно без привязки к клубу"
        disabled
      />
    );
  }
  return (
    <SearchPicker
      label="Участник"
      search={search}
      onSearch={setSearch}
      value={value}
      currentLabel={current.data ? personFullName(current.data) : undefined}
      options={(query.data?.items ?? []).map((person) => ({ value: person.id, label: personFullName(person) }))}
      onChange={onChange}
      placeholder="Выберите участника"
      loading={query.isLoading}
    />
  );
}

function InstructorPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 300);
  const query = useUsers({ search: debounced, role: "instructor" });
  const all = useAllUsersWithRole("instructor", Boolean(value));
  const current = all.data?.find((user) => user.id === value);
  return (
    <SearchPicker
      label="Инструктор"
      search={search}
      onSearch={setSearch}
      value={value}
      currentLabel={current ? userFullName(current) : undefined}
      options={(query.data?.items ?? []).map((user) => ({ value: user.id, label: userFullName(user) }))}
      onChange={onChange}
      placeholder="Выберите инструктора"
      loading={query.isLoading}
    />
  );
}

/** Every active Group (all pages), not only the first page. */
function GroupPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const query = useAllGroups("active");
  return (
    <SelectField
      label="Группа"
      value={value}
      placeholder={query.isLoading ? "Загружаем…" : "Выберите группу"}
      options={(query.data ?? [])
        .map((group) => ({ value: group.id, label: group.name }))
        .sort((a, b) => a.label.localeCompare(b.label, "ru"))}
      onChange={onChange}
    />
  );
}

export function RecipientFields({
  type,
  id,
  onChange,
}: {
  type: RecipientType;
  id: string;
  onChange: (type: RecipientType, id: string) => void;
}) {
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>Получатель</legend>
      <SelectField
        label="Тип получателя"
        value={type}
        options={RECIPIENT_TYPES.map((value) => ({ value, label: recipientTypeLabel(value) }))}
        onChange={(value) => onChange(value as RecipientType, "")}
      />
      {type === "member" ? <MemberPicker value={id} onChange={(value) => onChange(type, value)} /> : null}
      {type === "instructor" ? <InstructorPicker value={id} onChange={(value) => onChange(type, value)} /> : null}
      {type === "group" ? <GroupPicker value={id} onChange={(value) => onChange(type, value)} /> : null}
    </fieldset>
  );
}

/** Optional Event/Trip context (any club Event, §14 п.4). */
export function EventField({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 300);
  const query = useEventSearch(debounced);
  const current = useEvent(value || undefined);
  return (
    <SearchPicker
      label="Мероприятие"
      search={search}
      onSearch={setSearch}
      value={value}
      currentLabel={current.data?.title}
      options={(query.data?.items ?? []).map((event) => ({
        value: event.id,
        label: `${event.title} — ${new Date(event.start_at).toLocaleDateString("ru-RU")}`,
      }))}
      onChange={onChange}
      placeholder="Без мероприятия"
      loading={query.isLoading}
    />
  );
}

// --- issue lines -----------------------------------------------------------------

function AvailableInstances({
  itemId,
  selected,
  onChange,
}: {
  itemId: string;
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const query = useInventoryItemInstances(itemId, "available");
  if (query.isLoading) return <Loading label="Загружаем экземпляры…" />;
  if (query.isError) return <p className={styles.formError}>Не удалось загрузить экземпляры.</p>;
  const instances = query.data ?? [];
  if (instances.length === 0) return <p className={styles.muted}>Нет экземпляров в наличии.</p>;
  return (
    <fieldset className={styles.fieldset}>
      <legend className={styles.legend}>Экземпляры в наличии</legend>
      <ul className={styles.checkList}>
        {instances.map((instance) => (
          <li key={instance.id}>
            <label className={styles.checkItem}>
              <input
                type="checkbox"
                checked={selected.includes(instance.id)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, instance.id]
                      : selected.filter((id) => id !== instance.id),
                  )
                }
              />
              <span>
                {instance.inventory_number}
                {instance.manufacturer_serial_number ? ` · SN ${instance.manufacturer_serial_number}` : ""}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

/** One or more lines: an item and its quantity, or its specific
 * instances. An item chosen on one line is not offered on another (one
 * line per item in a request); the backend still validates. */
export function LinesEditor({
  lines,
  onChange,
  lookups,
}: {
  lines: DraftLine[];
  onChange: (lines: DraftLine[]) => void;
  lookups: InventoryLookups;
}) {
  const activeItems = [...lookups.items.values()]
    .filter((item) => item.status === "active")
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));

  function update(key: number, patch: Partial<DraftLine>) {
    onChange(lines.map((line) => (line.key === key ? { ...line, ...patch } : line)));
  }

  return (
    <div className={styles.form}>
      {lines.map((line, index) => {
        const item = lookups.items.get(line.itemId);
        const takenElsewhere = new Set(lines.filter((other) => other.key !== line.key).map((other) => other.itemId));
        return (
          <fieldset key={line.key} className={styles.fieldset}>
            <legend className={styles.legend}>{`Позиция ${index + 1}`}</legend>
            <SelectField
              label="Номенклатура"
              value={line.itemId}
              placeholder="Выберите позицию"
              options={activeItems
                .filter((candidate) => !takenElsewhere.has(candidate.id))
                .map((candidate) => ({
                  value: candidate.id,
                  label: `${candidate.name} · ${accountingModeLabel(candidate.accounting_mode).toLowerCase()}`,
                }))}
              onChange={(value) => update(line.key, { itemId: value, quantity: "", instanceIds: [] })}
            />
            {item?.accounting_mode === "quantity" ? (
              <Input
                label={`Количество, ${unitName(item, lookups) || "ед."}`}
                value={line.quantity}
                inputMode="numeric"
                onChange={(event) => update(line.key, { quantity: event.target.value })}
                hint="Места хранения распределит система."
              />
            ) : null}
            {item?.accounting_mode === "instance" ? (
              <AvailableInstances
                itemId={item.id}
                selected={line.instanceIds}
                onChange={(ids) => update(line.key, { instanceIds: ids })}
              />
            ) : null}
            {lines.length > 1 ? (
              <div className={styles.actions}>
                <Button
                  type="button"
                  variant="secondary"
                  icon="action.delete"
                  onClick={() => onChange(lines.filter((other) => other.key !== line.key))}
                >
                  Убрать позицию
                </Button>
              </div>
            ) : null}
          </fieldset>
        );
      })}
      <div className={styles.actions}>
        <Button type="button" variant="secondary" icon="action.add" onClick={() => onChange([...lines, newDraftLine()])}>
          Добавить позицию
        </Button>
      </div>
    </div>
  );
}

// --- create ----------------------------------------------------------------------

function IssueCreateDialog({ lookups, onClose }: { lookups: InventoryLookups; onClose: () => void }) {
  const [recipientType, setRecipientType] = useState<RecipientType>("member");
  const [recipientId, setRecipientId] = useState("");
  const [eventId, setEventId] = useState("");
  const [plannedReturn, setPlannedReturn] = useState("");
  const [comment, setComment] = useState("");
  const [lines, setLines] = useState<DraftLine[]>(() => [newDraftLine()]);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateInventoryIssue();
  const notify = useNotify();
  const navigate = useNavigate();
  const lineInput = draftLinesToInput(lines, lookups);

  function submit() {
    if (!lineInput) return;
    setError(null);
    create.mutate(
      {
        recipient_type: recipientType,
        recipient_id: recipientId,
        event_id: eventId || null,
        planned_return_date: plannedReturn || null,
        comment: comment.trim() || null,
        lines: lineInput,
      },
      {
        onSuccess: (issue) => {
          notify("success", "Имущество выдано");
          onClose();
          navigate(`/inventory/issues/${issue.id}`);
        },
        onError: (failure) => setError(mutationErrorMessage(failure)),
      },
    );
  }

  return (
    <FormDialog
      title="Выдать имущество"
      description="Выдача оформляется сразу — имущество уходит со склада при сохранении."
      submitLabel="Выдать"
      pending={create.isPending}
      error={error}
      canSubmit={Boolean(recipientId && lineInput)}
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
      <Input
        label="Плановая дата возврата"
        type="date"
        value={plannedReturn}
        onChange={(event) => setPlannedReturn(event.target.value)}
        hint="Необязательно, для информации."
      />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
      <LinesEditor lines={lines} onChange={setLines} lookups={lookups} />
    </FormDialog>
  );
}
