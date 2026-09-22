import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { FilterSelect, type FilterOption } from "../components/ui/FilterSelect";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { SearchInput } from "../components/ui/SearchInput";
import { Dialog } from "../components/ui/Dialog";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser, currentClubId, displayName } from "../api/auth";
import { saveBlob } from "../api/client";
import { useGroups } from "../api/groups";
import { useUsers, userFullName } from "../api/users";
import { personFullName, usePersons } from "../api/people";
import {
  useCreateEventDocumentRequirement,
  useDeleteEventDocumentRequirement,
  useEventDocumentRequirementCheck,
  useEventDocumentRequirements,
  useExportEventDocumentPackage,
  useUpdateEventDocumentRequirement,
  type DocumentPackageIncompleteEntry,
  type EventDocumentRequirement,
} from "../api/documents";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  useCalendarRange,
  useCreateEvent,
  useEvent,
  useOccurrence,
  useRegisterForEvent,
  useRescheduleOccurrence,
  useUpdateEvent,
  useWithdrawFromEvent,
  type CalendarItem,
  type EventDetail,
  type EventFields,
} from "../api/events";
import {
  documentRequirementResultIcon,
  documentRequirementResultLabel,
  documentTypeLabel,
  eventStatusIcon,
  eventStatusLabel,
  eventTypeLabel,
  CANONICAL_EVENT_TYPES,
  type EventStatus,
} from "../domain/statusMapping";
import {
  addDays,
  addMonths,
  buildMonthGrid,
  formatDateParam,
  formatDayMonth,
  formatTime,
  isSameDay,
  monthLabel,
  monthRange,
  parseDateParam,
  startOfDay,
  toDatetimeLocalValue,
  type MonthGridDay,
} from "../domain/calendarDate";
import { useMediaQuery } from "../hooks/useMediaQuery";
import styles from "./EventsPage.module.css";

const MOBILE_QUERY = "(max-width: 767.98px)";

/** Calendar-visible statuses only (events-api.md §16 §"Calendar status
 * visibility") — `draft`/`archived` never appear in `/events/calendar`, so
 * offering them as a filter would only ever produce an empty result. */
const CALENDAR_STATUSES: readonly EventStatus[] = ["published", "in_progress", "completed", "cancelled"];

const STATUS_FILTER_OPTIONS: FilterOption[] = [
  { value: "", label: "Все статусы" },
  ...CALENDAR_STATUSES.map((status) => ({ value: status, label: eventStatusLabel(status) })),
];

const TYPE_FILTER_OPTIONS: FilterOption[] = [
  { value: "", label: "Все типы" },
  ...CANONICAL_EVENT_TYPES.map((type) => ({ value: type, label: eventTypeLabel(type) })),
];

function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

// --- URL state ---------------------------------------------------------

type CalendarFiltersState = {
  group_id: string;
  event_type: string;
  status: string;
  user_id: string;
};

const EMPTY_FILTERS: CalendarFiltersState = { group_id: "", event_type: "", status: "", user_id: "" };

function useCalendarUrlState() {
  const [searchParams, setSearchParams] = useSearchParams();

  const selectedDate = useMemo(
    () => parseDateParam(searchParams.get("date")) ?? startOfDay(new Date()),
    [searchParams],
  );

  const filters: CalendarFiltersState = useMemo(
    () => ({
      group_id: searchParams.get("group_id") ?? "",
      event_type: searchParams.get("event_type") ?? "",
      status: searchParams.get("status") ?? "",
      user_id: searchParams.get("user_id") ?? "",
    }),
    [searchParams],
  );

  // Every one of these pushes a new history entry (react-router's default
  // `setSearchParams` navigation — `replace` is never passed) rather than
  // replacing the current one. ADR-0036 / TH-0105 requires that browser
  // Back/Forward step through calendar state (date, month, filters,
  // Reset), not just refresh/bookmark/share — `{ replace: true }` would
  // make every navigation invisible to history and break Back/Forward,
  // so it must not be used here.

  function selectDate(date: Date) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("date", formatDateParam(date));
      return next;
    });
  }

  function setFilter(key: keyof CalendarFiltersState, value: string) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (!value) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
      return next;
    });
  }

  function resetFilters() {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      (Object.keys(EMPTY_FILTERS) as Array<keyof CalendarFiltersState>).forEach((key) =>
        next.delete(key),
      );
      return next;
    });
  }

  return { selectedDate, filters, selectDate, setFilter, resetFilters };
}

// --- Page ----------------------------------------------------------------

export function EventsPage() {
  const { selectedDate, filters, selectDate, setFilter, resetFilters } = useCalendarUrlState();
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const notify = useNotify();

  const meQuery = useCurrentUser();
  const clubId = currentClubId(meQuery.data);
  const currentUserId = meQuery.data?.user.id;

  const groupsQuery = useGroups({ status: "active" });

  const range = useMemo(() => monthRange(selectedDate), [selectedDate]);
  const calendarQuery = useCalendarRange({
    from: range.from,
    to: range.to,
    group_id: filters.group_id || undefined,
    event_type: filters.event_type || undefined,
    status: filters.status || undefined,
    user_id: filters.user_id || undefined,
  });

  const itemsByDay = useMemo(() => {
    const map = new Map<string, CalendarItem[]>();
    for (const item of calendarQuery.data ?? []) {
      const key = formatDateParam(new Date(item.start_at));
      const bucket = map.get(key);
      if (bucket) bucket.push(item);
      else map.set(key, [item]);
    }
    return map;
  }, [calendarQuery.data]);

  const selectedDayItems = itemsByDay.get(formatDateParam(selectedDate)) ?? [];
  const isRangeEmpty = calendarQuery.isSuccess && (calendarQuery.data?.length ?? 0) === 0;

  const [createOpen, setCreateOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<CalendarItem | null>(null);
  const [editingItem, setEditingItem] = useState<CalendarItem | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  // The picker dialog knows a selected instructor's name at the moment it
  // is picked; the URL only ever stores the bare `user_id`. Kept in local
  // state (not derived) so the name survives re-renders within this
  // session — but only for the id it was learned for: if `filters.user_id`
  // ever points elsewhere (Reset, Back/Forward, a fresh page load), the id
  // guard below falls back to a neutral label rather than showing a stale
  // or fabricated name. There is no `GET /users/{id}` to re-resolve a name
  // from a bare id (docs/05-api/users-api.md — list/search only), so this
  // is a deliberate, disclosed limitation, not an oversight.
  const [pickedInstructor, setPickedInstructor] = useState<{ id: string; name: string } | null>(
    null,
  );

  const filtersActive = Boolean(
    filters.group_id || filters.event_type || filters.status || filters.user_id,
  );
  const isMineSelected = Boolean(currentUserId) && filters.user_id === currentUserId;
  const selectedInstructorLabel = !filters.user_id
    ? null
    : isMineSelected && meQuery.data
      ? displayName(meQuery.data.user)
      : pickedInstructor && pickedInstructor.id === filters.user_id
        ? pickedInstructor.name
        : "Инструктор выбран";

  function goToMonth(delta: number) {
    selectDate(addMonths(selectedDate, delta));
  }

  function goToday() {
    selectDate(startOfDay(new Date()));
  }

  return (
    <div>
      <PageHeader
        title="Календарь"
        description="События и расписание клуба."
        actions={
          <Button
            variant="primary"
            icon="action.add"
            disabled={!clubId}
            title={clubId ? undefined : "Недоступно без привязки к клубу"}
            onClick={() => setCreateOpen(true)}
          >
            Создать событие
          </Button>
        }
      />

      <FiltersToolbar
        filters={filters}
        groupOptions={groupsQuery.data?.items ?? []}
        onChange={setFilter}
        onReset={resetFilters}
        filtersActive={filtersActive}
        clubId={clubId}
        selectedInstructorLabel={selectedInstructorLabel}
        isMineSelected={isMineSelected}
        onToggleMine={(checked) => setFilter("user_id", checked && currentUserId ? currentUserId : "")}
        onOpenPicker={() => setPickerOpen(true)}
        onClearInstructor={() => setFilter("user_id", "")}
      />

      <InstructorPickerDialog
        open={pickerOpen}
        clubId={clubId}
        onClose={() => setPickerOpen(false)}
        onSelect={(user) => {
          setPickedInstructor(user);
          setFilter("user_id", user.id);
          setPickerOpen(false);
        }}
      />

      {calendarQuery.isError ? (
        <ErrorState
          illustration={calendarQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить события"
          description={calendarQuery.error.message}
          action={
            <Button variant="secondary" onClick={() => calendarQuery.refetch()}>
              Повторить
            </Button>
          }
        />
      ) : isMobile ? (
        <MobileAgenda
          selectedDate={selectedDate}
          onSelectDate={selectDate}
          onPrevMonth={() => goToMonth(-1)}
          onNextMonth={() => goToMonth(1)}
          onToday={goToday}
          isInitialLoading={calendarQuery.isLoading}
          isFetching={calendarQuery.isFetching}
          dayItems={selectedDayItems}
          isRangeEmpty={isRangeEmpty}
          onOpenItem={setDetailItem}
        />
      ) : (
        <DesktopCalendar
          selectedDate={selectedDate}
          onSelectDate={selectDate}
          onPrevMonth={() => goToMonth(-1)}
          onNextMonth={() => goToMonth(1)}
          onToday={goToday}
          isInitialLoading={calendarQuery.isLoading}
          isFetching={calendarQuery.isFetching}
          itemsByDay={itemsByDay}
          dayItems={selectedDayItems}
          isRangeEmpty={isRangeEmpty}
          onOpenItem={setDetailItem}
        />
      )}

      <EventFormDialog
        open={createOpen}
        mode={{ kind: "create", clubId: clubId ?? "", defaultDate: selectedDate }}
        onClose={() => setCreateOpen(false)}
        onSaved={() => {
          notify("success", "Событие создано");
          setCreateOpen(false);
        }}
      />

      {detailItem ? (
        <EventDetailDialog
          item={detailItem}
          onClose={() => setDetailItem(null)}
          onEdit={() => {
            setEditingItem(detailItem);
            setDetailItem(null);
          }}
        />
      ) : null}

      {editingItem ? (
        <EventFormDialog
          open
          mode={
            editingItem.kind === "event"
              ? { kind: "edit-event", eventId: editingItem.id }
              : {
                  kind: "edit-occurrence",
                  occurrenceId: editingItem.id,
                  seriesId: editingItem.series_id ?? "",
                }
          }
          onClose={() => setEditingItem(null)}
          onSaved={() => {
            notify("success", "Событие обновлено");
            setEditingItem(null);
          }}
        />
      ) : null}
    </div>
  );
}

// --- Filters toolbar -------------------------------------------------------

function FiltersToolbar({
  filters,
  groupOptions,
  onChange,
  onReset,
  filtersActive,
  clubId,
  selectedInstructorLabel,
  isMineSelected,
  onToggleMine,
  onOpenPicker,
  onClearInstructor,
}: {
  filters: CalendarFiltersState;
  groupOptions: Array<{ id: string; name: string }>;
  onChange: (key: keyof CalendarFiltersState, value: string) => void;
  onReset: () => void;
  filtersActive: boolean;
  clubId: string | null;
  selectedInstructorLabel: string | null;
  isMineSelected: boolean;
  onToggleMine: (checked: boolean) => void;
  onOpenPicker: () => void;
  onClearInstructor: () => void;
}) {
  const groupFilterOptions: FilterOption[] = [
    { value: "", label: "Все группы" },
    ...groupOptions.map((group) => ({ value: group.id, label: group.name })),
  ];

  return (
    <div className={styles.toolbar}>
      <FilterSelect
        label="Группа"
        value={filters.group_id}
        options={groupFilterOptions}
        onChange={(value) => onChange("group_id", value)}
      />
      <FilterSelect
        label="Тип"
        value={filters.event_type}
        options={TYPE_FILTER_OPTIONS}
        onChange={(value) => onChange("event_type", value)}
      />
      <FilterSelect
        label="Статус"
        value={filters.status}
        options={STATUS_FILTER_OPTIONS}
        onChange={(value) => onChange("status", value)}
      />
      <div className={styles.instructorControl}>
        {selectedInstructorLabel ? (
          <div className={styles.selectedInstructor}>
            <span>{selectedInstructorLabel}</span>
            <Button variant="secondary" onClick={onOpenPicker}>
              Изменить
            </Button>
            <Button variant="secondary" onClick={onClearInstructor}>
              Очистить
            </Button>
          </div>
        ) : (
          <Button
            variant="secondary"
            disabled={!clubId}
            title={clubId ? undefined : "Недоступно без привязки к клубу"}
            onClick={onOpenPicker}
          >
            Выбрать инструктора
          </Button>
        )}
      </div>
      {/* A convenience shortcut over the same `user_id` filter (self's own
       * id) — not a separate/alternative filter and not itself "the"
       * Instructor/user filter (that is the picker above, backed by the
       * real `GET /users` directory, TH-0107). */}
      <label className={styles.mineToggle}>
        <input
          type="checkbox"
          checked={isMineSelected}
          onChange={(event) => onToggleMine(event.target.checked)}
        />
        Только мои события
      </label>
      <Button variant="secondary" onClick={onReset} disabled={!filtersActive}>
        Сбросить
      </Button>
    </div>
  );
}

function InstructorPickerDialog({
  open,
  clubId,
  onClose,
  onSelect,
}: {
  open: boolean;
  clubId: string | null;
  onClose: () => void;
  onSelect: (user: { id: string; name: string }) => void;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const usersQuery = useUsers({
    role: "instructor",
    club_id: clubId ?? undefined,
    search: debouncedSearch,
    enabled: open,
  });

  function handleClose() {
    setSearch("");
    onClose();
  }

  return (
    <Dialog open={open} title="Выбрать инструктора" onClose={handleClose}>
      <div className={styles.form}>
        <SearchInput
          label="Поиск инструктора"
          value={search}
          onChange={setSearch}
          placeholder="Например, «Иванова»"
        />
        {usersQuery.isLoading ? <Loading label="Загружаем инструкторов…" /> : null}
        {usersQuery.isError ? (
          <ErrorState
            illustration="error"
            title="Не удалось загрузить инструкторов"
            description={usersQuery.error.message}
            action={
              <Button variant="secondary" onClick={() => usersQuery.refetch()}>
                Повторить
              </Button>
            }
          />
        ) : null}
        {usersQuery.isSuccess ? (
          <ul className={styles.pickerList}>
            {usersQuery.data.items.map((user) => (
              <li key={user.id}>
                <button
                  type="button"
                  className={styles.pickerItem}
                  onClick={() => {
                    onSelect({ id: user.id, name: userFullName(user) });
                    setSearch("");
                  }}
                >
                  {userFullName(user)}
                </button>
              </li>
            ))}
            {usersQuery.data.items.length === 0 ? (
              <li className={styles.pickerEmpty}>Ничего не найдено</li>
            ) : null}
          </ul>
        ) : null}
      </div>
    </Dialog>
  );
}

// --- Desktop / tablet composition ------------------------------------------

function DesktopCalendar({
  selectedDate,
  onSelectDate,
  onPrevMonth,
  onNextMonth,
  onToday,
  isInitialLoading,
  isFetching,
  itemsByDay,
  dayItems,
  isRangeEmpty,
  onOpenItem,
}: {
  selectedDate: Date;
  onSelectDate: (date: Date) => void;
  onPrevMonth: () => void;
  onNextMonth: () => void;
  onToday: () => void;
  isInitialLoading: boolean;
  isFetching: boolean;
  itemsByDay: Map<string, CalendarItem[]>;
  dayItems: CalendarItem[];
  isRangeEmpty: boolean;
  onOpenItem: (item: CalendarItem) => void;
}) {
  const grid = useMemo(() => buildMonthGrid(selectedDate), [selectedDate]);

  return (
    <div className={styles.desktopLayout}>
      <div className={styles.calendarPanel}>
        <CalendarNav
          label={monthLabel(selectedDate)}
          onPrev={onPrevMonth}
          onNext={onNextMonth}
          onToday={onToday}
          isFetching={isFetching}
        />
        {isInitialLoading ? (
          <MonthGridSkeleton />
        ) : (
          <div className={styles.monthGrid} role="grid" aria-label={monthLabel(selectedDate)}>
            {grid.map((day) => (
              <MonthCell
                key={day.key}
                day={day}
                items={itemsByDay.get(day.key) ?? []}
                selected={isSameDay(day.date, selectedDate)}
                onSelect={() => onSelectDate(day.date)}
              />
            ))}
          </div>
        )}
      </div>
      <div className={styles.dayPanel}>
        <h2 className={styles.dayPanelTitle}>{formatDayMonth(selectedDate.toISOString())}</h2>
        <DayEventList
          items={dayItems}
          isRangeEmpty={isRangeEmpty}
          isLoading={isInitialLoading}
          isFetching={isFetching}
          onOpenItem={onOpenItem}
        />
      </div>
    </div>
  );
}

function CalendarNav({
  label,
  onPrev,
  onNext,
  onToday,
  isFetching,
}: {
  label: string;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
  isFetching: boolean;
}) {
  return (
    <div className={styles.calendarNav}>
      <div className={styles.calendarNavButtons}>
        <Button variant="secondary" onClick={onPrev} aria-label="Предыдущий месяц">
          ‹
        </Button>
        <Button variant="secondary" onClick={onToday}>
          Сегодня
        </Button>
        <Button variant="secondary" onClick={onNext} aria-label="Следующий месяц">
          ›
        </Button>
      </div>
      <span className={styles.calendarNavLabel}>
        {label}
        {isFetching ? <span className={styles.fetchingIndicator} role="status" aria-label="Обновление…" /> : null}
      </span>
    </div>
  );
}

const MAX_CHIPS_PER_CELL = 2;

function chipStatusClass(status: string): string {
  switch (status) {
    case "in_progress":
      return styles.chipStatusOngoing;
    case "completed":
      return styles.chipStatusCompleted;
    case "cancelled":
      return styles.chipStatusCancelled;
    default:
      return styles.chipStatusPlanned;
  }
}

function MonthCell({
  day,
  items,
  selected,
  onSelect,
}: {
  day: MonthGridDay;
  items: CalendarItem[];
  selected: boolean;
  onSelect: () => void;
}) {
  const visible = items.slice(0, MAX_CHIPS_PER_CELL);
  const overflow = items.length - visible.length;

  return (
    <button
      type="button"
      role="gridcell"
      aria-current={day.isToday ? "date" : undefined}
      aria-selected={selected}
      onClick={onSelect}
      className={[
        styles.monthCell,
        day.inCurrentMonth ? "" : styles.monthCellOutside,
        day.isToday ? styles.monthCellToday : "",
        selected ? styles.monthCellSelected : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span className={styles.monthCellDate}>{day.date.getDate()}</span>
      <span className={styles.monthCellChips}>
        {visible.map((item) => (
          <span key={item.id} className={[styles.chip, chipStatusClass(item.status)].join(" ")}>
            {item.kind === "occurrence" ? <span aria-hidden="true">↻ </span> : null}
            {formatTime(item.start_at)} {item.title}
          </span>
        ))}
        {overflow > 0 ? <span className={styles.chipOverflow}>+{overflow}</span> : null}
      </span>
    </button>
  );
}

function MonthGridSkeleton() {
  return (
    <div className={styles.monthGrid} aria-hidden="true">
      {Array.from({ length: 42 }, (_, index) => (
        <div key={index} className={`${styles.monthCell} ${styles.skeletonCell}`} />
      ))}
    </div>
  );
}

// --- Mobile composition ------------------------------------------------

function MobileAgenda({
  selectedDate,
  onSelectDate,
  onPrevMonth,
  onNextMonth,
  onToday,
  isInitialLoading,
  isFetching,
  dayItems,
  isRangeEmpty,
  onOpenItem,
}: {
  selectedDate: Date;
  onSelectDate: (date: Date) => void;
  onPrevMonth: () => void;
  onNextMonth: () => void;
  onToday: () => void;
  isInitialLoading: boolean;
  isFetching: boolean;
  dayItems: CalendarItem[];
  isRangeEmpty: boolean;
  onOpenItem: (item: CalendarItem) => void;
}) {
  return (
    <div className={styles.mobileLayout}>
      <CalendarNav
        label={monthLabel(selectedDate)}
        onPrev={onPrevMonth}
        onNext={onNextMonth}
        onToday={onToday}
        isFetching={isFetching}
      />
      <div className={styles.dayStepper}>
        <Button
          variant="secondary"
          aria-label="Предыдущий день"
          onClick={() => onSelectDate(addDays(selectedDate, -1))}
        >
          ‹
        </Button>
        <span className={styles.dayStepperLabel}>{formatDayMonth(selectedDate.toISOString())}</span>
        <Button
          variant="secondary"
          aria-label="Следующий день"
          onClick={() => onSelectDate(addDays(selectedDate, 1))}
        >
          ›
        </Button>
      </div>
      <DayEventList
        items={dayItems}
        isRangeEmpty={isRangeEmpty}
        isLoading={isInitialLoading}
        isFetching={isFetching}
        onOpenItem={onOpenItem}
      />
    </div>
  );
}

// --- Shared day list ---------------------------------------------------

function DayEventList({
  items,
  isRangeEmpty,
  isLoading,
  isFetching,
  onOpenItem,
}: {
  items: CalendarItem[];
  isRangeEmpty: boolean;
  isLoading: boolean;
  isFetching: boolean;
  onOpenItem: (item: CalendarItem) => void;
}) {
  if (isLoading) {
    return <Loading label="Загружаем события…" />;
  }
  // A range change (month/filter navigation) keeps showing whatever the
  // previously loaded range placed here (ADR-0036: "previous data remains
  // visible ... light loading indication"). If the *new* range hasn't
  // settled yet and this day/range currently reads empty, that emptiness
  // may just be leftover from the previous, unrelated range — show a
  // loading state instead of asserting "empty" prematurely.
  if (isFetching && items.length === 0) {
    return <Loading label="Загружаем события…" />;
  }
  if (isRangeEmpty) {
    return <EmptyState illustration="no-results" title="В этом периоде нет событий" />;
  }
  if (items.length === 0) {
    return <EmptyState illustration="no-results" title="На этот день событий нет" />;
  }
  return (
    <ul className={styles.dayList}>
      {items.map((item) => (
        <li key={item.id}>
          <CalendarEventRow item={item} onOpen={() => onOpenItem(item)} />
        </li>
      ))}
    </ul>
  );
}

function CalendarEventRow({ item, onOpen }: { item: CalendarItem; onOpen: () => void }) {
  const cancelled = item.status === "cancelled";
  return (
    <button type="button" className={styles.eventRow} onClick={onOpen}>
      <span className={styles.eventRowTime}>
        {formatTime(item.start_at)}–{formatTime(item.end_at)}
      </span>
      <span className={styles.eventRowMain}>
        <span className={cancelled ? styles.eventRowTitleCancelled : styles.eventRowTitle}>
          {item.kind === "occurrence" ? <RecurringBadge /> : null}
          {item.title}
        </span>
        <span className={styles.eventRowMeta}>{eventTypeLabel(item.event_type)}</span>
      </span>
      <StatusBadge status={eventStatusIcon(item.status as EventStatus)} label={eventStatusLabel(item.status as EventStatus)} />
    </button>
  );
}

function RecurringBadge() {
  return (
    <span className={styles.recurringBadge} title="Повторяющееся событие">
      ↻
    </span>
  );
}

// --- Detail dialog -------------------------------------------------------

function EventDetailDialog({
  item,
  onClose,
  onEdit,
}: {
  item: CalendarItem;
  onClose: () => void;
  onEdit: () => void;
}) {
  const eventQuery = useEvent(item.kind === "event" ? item.id : undefined);
  const occurrenceQuery = useOccurrence(item.kind === "occurrence" ? item.id : undefined);
  const query = item.kind === "event" ? eventQuery : occurrenceQuery;
  const cancelled = item.status === "cancelled";
  const [documentsOpen, setDocumentsOpen] = useState(false);

  return (
    <Dialog open title={item.title} description={eventTypeLabel(item.event_type)} onClose={onClose}>
      <div className={styles.detailBody}>
        <StatusBadge status={eventStatusIcon(item.status as EventStatus)} label={eventStatusLabel(item.status as EventStatus)} />
        {item.kind === "occurrence" ? (
          <p className={styles.detailRecurring}>
            <RecurringBadge /> Повторяющееся событие
          </p>
        ) : null}
        <dl className={styles.detailList}>
          <div>
            <dt>Начало</dt>
            <dd>
              {formatDayMonth(item.start_at)}, {formatTime(item.start_at)}
            </dd>
          </div>
          <div>
            <dt>Окончание</dt>
            <dd>
              {formatDayMonth(item.end_at)}, {formatTime(item.end_at)}
            </dd>
          </div>
        </dl>
        {item.description ? <p>{item.description}</p> : null}
        {cancelled && item.cancellation_reason ? (
          <p className={styles.cancellationReason}>Причина отмены: {item.cancellation_reason}</p>
        ) : null}
        {query.isLoading ? <Loading label="Загружаем подробности…" /> : null}
        {query.isError ? (
          <ErrorState illustration="error" title="Не удалось загрузить подробности" description={query.error.message} />
        ) : null}
        {query.isSuccess && item.kind === "event" && eventQuery.data ? (
          <EventLocation event={eventQuery.data} />
        ) : null}
        {query.isSuccess && item.kind === "event" && eventQuery.data ? (
          <EventSelfRegistration event={eventQuery.data} />
        ) : null}
      </div>
      <div className={styles.detailActions}>
        <Button variant="primary" icon="action.edit" onClick={onEdit}>
          Редактировать
        </Button>
        {item.kind === "event" ? (
          <Button variant="secondary" onClick={() => setDocumentsOpen(true)}>
            Документы для соревнования
          </Button>
        ) : null}
      </div>
      {item.kind === "event" ? (
        <EventDocumentPackageDialog
          open={documentsOpen}
          eventId={item.id}
          eventTitle={item.title}
          onClose={() => setDocumentsOpen(false)}
        />
      ) : null}
    </Dialog>
  );
}

// --- Competition document package workflow (TH-0117 / Issue #175) ---------
//
// «Документы для соревнования» — the explicit Event-scoped workflow for
// managing EventDocumentRequirement rows, checking one participant's
// document readiness, and exporting the protected competition document
// package. Every control here is always rendered for any signed-in
// viewer — this file never guesses who holds `event.manage`/
// `document.manage`/`document.export` from `role_assignments`; nothing
// in roles-and-permissions.md or ADR-0040 documents `document.*` as
// admin-only, so an `isAdmin` role check would assert an authorization
// rule this frontend has no basis for. The backend remains the sole
// enforcement point: a read (list requirements, check a participant)
// without the right permission surfaces its own 403 via the existing
// `ApiError` → `ErrorState`/toast handling below, and a mutation without
// it is rejected the same way, exactly like every other action in this
// app.
//
// KNOWN CONTRACT GAP: there is no implemented endpoint that lists an
// Event's participants (see api/documents.ts's `useEventDocument
// RequirementCheck` module comment) — the "participant requirement
// results" view here is therefore a deliberate, on-demand per-participant
// lookup (search a Person, then check their readiness for this Event),
// not an auto-loaded roster. The competition package export itself does
// not depend on this: it resolves the participant set entirely
// server-side.

function EventDocumentPackageDialog({
  open,
  eventId,
  eventTitle,
  onClose,
}: {
  open: boolean;
  eventId: string;
  eventTitle: string;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      title="Документы для соревнования"
      description={eventTitle}
      onClose={onClose}
      actions={
        <Button variant="secondary" onClick={onClose}>
          Закрыть
        </Button>
      }
    >
      <div className={styles.documentPackageBody}>
        <EventDocumentRequirementsSection eventId={eventId} />
        <ParticipantDocumentCheckSection eventId={eventId} />
        <DocumentPackageExportSection eventId={eventId} eventTitle={eventTitle} />
      </div>
    </Dialog>
  );
}

function EventDocumentRequirementsSection({ eventId }: { eventId: string }) {
  const requirementsQuery = useEventDocumentRequirements(eventId);
  const deleteRequirement = useDeleteEventDocumentRequirement();
  const notify = useNotify();
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<EventDocumentRequirement | null>(null);

  return (
    <section>
      <div className={styles.documentSectionHeader}>
        <h3>Требования к документам</h3>
        <Button variant="secondary" icon="action.add" onClick={() => setAddOpen(true)}>
          Добавить требование
        </Button>
      </div>

      {requirementsQuery.isLoading ? <Loading label="Загружаем требования…" /> : null}
      {requirementsQuery.isError ? (
        <ErrorState
          illustration={requirementsQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить требования"
          description={requirementsQuery.error.message}
        />
      ) : null}
      {requirementsQuery.isSuccess && requirementsQuery.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Требования к документам не заданы" />
      ) : null}
      {requirementsQuery.isSuccess && requirementsQuery.data.items.length > 0 ? (
        <ul className={styles.documentList}>
          {requirementsQuery.data.items.map((requirement) => (
            <RequirementRow
              key={requirement.id}
              eventId={eventId}
              requirement={requirement}
              onDelete={() => setDeleteTarget(requirement)}
            />
          ))}
        </ul>
      ) : null}

      <AddRequirementDialog
        open={addOpen}
        eventId={eventId}
        existingTypes={requirementsQuery.data?.items.map((r) => r.document_type) ?? []}
        onClose={() => setAddOpen(false)}
      />
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Удалить требование?"
        description={
          deleteTarget ? `Требование «${documentTypeLabel(deleteTarget.document_type)}» будет удалено.` : undefined
        }
        confirmLabel="Удалить"
        destructive
        pending={deleteRequirement.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (!deleteTarget) return;
          deleteRequirement.mutate(
            { eventId, requirementId: deleteTarget.id },
            {
              onSuccess: () => {
                notify("success", "Требование удалено");
                setDeleteTarget(null);
              },
              onError: (error) => notify("error", error.message),
            },
          );
        }}
      />
    </section>
  );
}

function RequirementRow({
  eventId,
  requirement,
  onDelete,
}: {
  eventId: string;
  requirement: EventDocumentRequirement;
  onDelete: () => void;
}) {
  const updateRequirement = useUpdateEventDocumentRequirement();
  const notify = useNotify();

  function toggleRequired() {
    updateRequirement.mutate(
      { eventId, requirementId: requirement.id, required: !requirement.required },
      { onError: (error) => notify("error", error.message) },
    );
  }

  return (
    <li className={styles.documentRow}>
      <div className={styles.documentRowMain}>
        <span>{documentTypeLabel(requirement.document_type)}</span>
      </div>
      <div className={styles.documentRowActions}>
        <label className={styles.mineToggle}>
          <input
            type="checkbox"
            checked={requirement.required}
            disabled={updateRequirement.isPending}
            onChange={toggleRequired}
          />
          Обязательно
        </label>
        <Button variant="destructive" icon="action.delete" onClick={onDelete}>
          Удалить
        </Button>
      </div>
    </li>
  );
}

function AddRequirementDialog({
  open,
  eventId,
  existingTypes,
  onClose,
}: {
  open: boolean;
  eventId: string;
  existingTypes: string[];
  onClose: () => void;
}) {
  const [documentType, setDocumentType] = useState("");
  const [required, setRequired] = useState(true);
  const createRequirement = useCreateEventDocumentRequirement();
  const notify = useNotify();

  function reset() {
    setDocumentType("");
    setRequired(true);
  }

  function handleClose() {
    reset();
    onClose();
  }

  const isDuplicate = existingTypes.includes(documentType.trim());

  function handleSubmit() {
    if (!documentType.trim() || isDuplicate) return;
    createRequirement.mutate(
      { eventId, document_type: documentType.trim(), required },
      {
        onSuccess: () => {
          notify("success", "Требование добавлено");
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Добавить требование"
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={createRequirement.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!documentType.trim() || isDuplicate || createRequirement.isPending}
          >
            Добавить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input
          label="Тип документа"
          value={documentType}
          onChange={(e) => setDocumentType(e.target.value)}
          placeholder="medical_certificate"
          hint={
            isDuplicate
              ? "Требование для этого типа документа уже существует"
              : `«medical_certificate» отображается как «${documentTypeLabel("medical_certificate")}»`
          }
          required
        />
        <label className={styles.mineToggle}>
          <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
          Обязательно для допуска
        </label>
      </div>
    </Dialog>
  );
}

function ParticipantDocumentCheckSection({ eventId }: { eventId: string }) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);
  const searchQuery = usePersons({ page: 1, search: debouncedSearch });
  const checkQuery = useEventDocumentRequirementCheck(eventId, selected?.id);

  return (
    <section>
      <h3>Проверка документов участника</h3>
      {selected ? (
        <div className={styles.selectedInstructor}>
          <span>{selected.name}</span>
          <Button variant="secondary" onClick={() => setSelected(null)}>
            Изменить выбор
          </Button>
        </div>
      ) : (
        <>
          <SearchInput
            label="Поиск участника"
            value={search}
            onChange={setSearch}
            placeholder="Например, «Иванова»"
          />
          {searchQuery.isSuccess && debouncedSearch ? (
            <ul className={styles.pickerList}>
              {searchQuery.data.items.map((candidate) => (
                <li key={candidate.id}>
                  <button
                    type="button"
                    className={styles.pickerItem}
                    onClick={() => setSelected({ id: candidate.id, name: personFullName(candidate) })}
                  >
                    {personFullName(candidate)}
                  </button>
                </li>
              ))}
              {searchQuery.data.items.length === 0 ? (
                <li className={styles.pickerEmpty}>Ничего не найдено</li>
              ) : null}
            </ul>
          ) : null}
        </>
      )}

      {selected && checkQuery.isLoading ? <Loading label="Проверяем документы…" /> : null}
      {selected && checkQuery.isError ? (
        <ErrorState
          illustration={checkQuery.error.status === 403 ? "403" : checkQuery.error.status === 404 ? "404" : "error"}
          title="Не удалось проверить документы"
          description={checkQuery.error.message}
        />
      ) : null}
      {selected && checkQuery.isSuccess && checkQuery.data.requirements.length === 0 ? (
        <EmptyState illustration="no-results" title="Требования к документам не заданы" />
      ) : null}
      {selected && checkQuery.isSuccess && checkQuery.data.requirements.length > 0 ? (
        <ul className={styles.documentList}>
          {checkQuery.data.requirements.map((check) => (
            <li key={check.document_type} className={styles.documentRow}>
              <div className={styles.documentRowMain}>
                <span>{documentTypeLabel(check.document_type)}</span>
                <span className={styles.rowSecondary}>{check.required ? "Обязательно" : "Опционально"}</span>
              </div>
              <StatusBadge
                status={documentRequirementResultIcon(check.result)}
                label={documentRequirementResultLabel(check.result)}
              />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function DocumentPackageExportSection({ eventId, eventTitle }: { eventId: string; eventTitle: string }) {
  const notify = useNotify();
  const exportPackage = useExportEventDocumentPackage();
  const [incomplete, setIncomplete] = useState<DocumentPackageIncompleteEntry[] | null>(null);

  function runExport(confirmIncomplete: boolean) {
    exportPackage.mutate(
      { eventId, confirmIncomplete },
      {
        onSuccess: ({ blob, filename }) => {
          saveBlob(blob, filename ?? `competition-documents-${eventTitle}.zip`);
          setIncomplete(null);
          notify("success", "Пакет документов сформирован");
        },
        onError: (error) => {
          if (error.status === 409 && error.code === "document_package_incomplete") {
            const details = error.details as { incomplete?: DocumentPackageIncompleteEntry[] } | undefined;
            setIncomplete(details?.incomplete ?? []);
            return;
          }
          notify("error", error.message);
        },
      },
    );
  }

  return (
    <section>
      <h3>Экспорт пакета документов</h3>
      <div className={styles.tabActions}>
        <Button
          variant="primary"
          icon="action.download"
          disabled={exportPackage.isPending}
          onClick={() => runExport(false)}
        >
          {exportPackage.isPending ? "Формирование…" : "Экспортировать пакет документов"}
        </Button>
      </div>

      <Dialog
        open={incomplete !== null}
        title="Пакет документов неполный"
        description="У части участников отсутствует или истёк обязательный документ."
        onClose={() => setIncomplete(null)}
        actions={
          <>
            <Button variant="secondary" onClick={() => setIncomplete(null)} disabled={exportPackage.isPending}>
              Отмена
            </Button>
            <Button
              variant="destructive"
              disabled={exportPackage.isPending}
              onClick={() => runExport(true)}
            >
              {exportPackage.isPending ? "Формирование…" : "Всё равно сформировать пакет"}
            </Button>
          </>
        }
      >
        {incomplete && incomplete.length > 0 ? (
          <ul className={styles.documentList}>
            {incomplete.map((entry, index) => (
              <li key={index} className={styles.documentRow}>
                <div className={styles.documentRowMain}>
                  <span>{entry.participant_display_name}</span>
                  <span className={styles.rowSecondary}>
                    {documentTypeLabel(entry.document_type)} · {entry.required ? "Обязательно" : "Опционально"}
                  </span>
                </div>
                <StatusBadge
                  status={documentRequirementResultIcon(entry.result)}
                  label={documentRequirementResultLabel(entry.result)}
                />
              </li>
            ))}
          </ul>
        ) : null}
      </Dialog>
    </section>
  );
}

/** TH-0108.2 / ADR-0037: self-registration action for the currently
 * viewed Event. Renders nothing for a non-`published` Event (draft/
 * completed/cancelled/archived) — self-registration is unavailable in
 * every one of those states. Eligibility itself is never computed here:
 * this only reflects `my_registration_status`, which the backend already
 * resolved from the authenticated viewer's own Person; an ineligible
 * click still surfaces as an ordinary backend rejection via toast, never
 * a client-side guess. */
function EventSelfRegistration({ event }: { event: EventDetail }) {
  const notify = useNotify();
  const register = useRegisterForEvent();
  const withdraw = useWithdrawFromEvent();

  if (event.status !== "published") return null;

  const registered = event.my_registration_status === "registered";
  const pending = register.isPending || withdraw.isPending;

  const handleRegister = () => {
    register.mutate(event.id, {
      onSuccess: () => notify("success", "Вы записаны на мероприятие"),
      onError: (error) => notify("error", error.message),
    });
  };

  const handleWithdraw = () => {
    withdraw.mutate(event.id, {
      onSuccess: () => notify("success", "Запись отменена"),
      onError: (error) => notify("error", error.message),
    });
  };

  return (
    <div className={styles.registrationRow}>
      {registered ? (
        <>
          <StatusBadge status="status.success" label="Вы записаны" />
          <Button variant="destructive" icon="action.cancel" onClick={handleWithdraw} disabled={pending}>
            Отменить запись
          </Button>
        </>
      ) : (
        <Button variant="primary" icon="action.confirm" onClick={handleRegister} disabled={pending}>
          Записаться
        </Button>
      )}
    </div>
  );
}

function EventLocation({ event }: { event: { location_name: string | null; location_address: string | null } }) {
  if (!event.location_name && !event.location_address) return null;
  return (
    <dl className={styles.detailList}>
      <div>
        <dt>Место</dt>
        <dd>{[event.location_name, event.location_address].filter(Boolean).join(", ")}</dd>
      </div>
    </dl>
  );
}

// --- Create / edit form dialog -------------------------------------------

type EventFormMode =
  | { kind: "create"; clubId: string; defaultDate: Date }
  | { kind: "edit-event"; eventId: string }
  | { kind: "edit-occurrence"; occurrenceId: string; seriesId: string };

function EventFormDialog({
  open,
  mode,
  onClose,
  onSaved,
}: {
  open: boolean;
  mode: EventFormMode;
  onClose: () => void;
  onSaved: () => void;
}) {
  const notify = useNotify();
  const createEvent = useCreateEvent();
  const updateEvent = useUpdateEvent();
  const rescheduleOccurrence = useRescheduleOccurrence();

  const eventQuery = useEvent(mode.kind === "edit-event" ? mode.eventId : undefined);
  const occurrenceQuery = useOccurrence(mode.kind === "edit-occurrence" ? mode.occurrenceId : undefined);

  // TH-0108 / ADR-0037 §1-§2: Group targeting and responsible-instructor
  // assignment are only meaningful for an ordinary Event, never a single
  // recurring occurrence (EventGroupTarget/EventStaffAssignment belong to
  // the parent Event, not per-occurrence — ADR-0033's model has no such
  // concept), so this section is hidden for `edit-occurrence`, exactly
  // like the existing location fields already are.
  const isTargetingEditable = mode.kind === "create" || mode.kind === "edit-event";
  const targetingClubId =
    mode.kind === "create" ? mode.clubId : mode.kind === "edit-event" ? (eventQuery.data?.club_id ?? null) : null;

  const groupsQuery = useGroups({ status: "active" });
  // Resolves readable names for the club's instructors so already-assigned
  // ones (edit mode) show a name, not a bare id, before the picker below
  // has ever been searched — the same `GET /users` User Directory the
  // picker itself queries, no new endpoint.
  const instructorDirectoryQuery = useUsers({
    role: "instructor",
    club_id: targetingClubId ?? undefined,
    enabled: isTargetingEditable && open,
  });

  const loadingExisting =
    (mode.kind === "edit-event" && eventQuery.isLoading) ||
    (mode.kind === "edit-occurrence" && occurrenceQuery.isLoading);

  const initial = useMemo(() => {
    if (mode.kind === "create") {
      const start = new Date(mode.defaultDate);
      start.setHours(10, 0, 0, 0);
      const end = new Date(start);
      end.setHours(start.getHours() + 1);
      return {
        title: "",
        description: "",
        eventType: "lesson",
        start: toDatetimeLocalValue(start),
        end: toDatetimeLocalValue(end),
        locationName: "",
        locationAddress: "",
        groupIds: [] as string[],
        instructorIds: [] as string[],
      };
    }
    if (mode.kind === "edit-event" && eventQuery.data) {
      const event = eventQuery.data;
      return {
        title: event.title,
        description: event.description ?? "",
        eventType: event.event_type,
        start: toDatetimeLocalValue(new Date(event.start_at)),
        end: toDatetimeLocalValue(new Date(event.end_at)),
        locationName: event.location_name ?? "",
        locationAddress: event.location_address ?? "",
        groupIds: event.group_ids,
        instructorIds: event.instructor_ids,
      };
    }
    if (mode.kind === "edit-occurrence" && occurrenceQuery.data) {
      const occurrence = occurrenceQuery.data;
      return {
        title: occurrence.name,
        description: occurrence.description ?? "",
        eventType: occurrence.event_type,
        start: toDatetimeLocalValue(new Date(occurrence.starts_at)),
        end: toDatetimeLocalValue(new Date(occurrence.ends_at)),
        locationName: "",
        locationAddress: "",
        groupIds: [] as string[],
        instructorIds: [] as string[],
      };
    }
    return null;
  }, [mode, eventQuery.data, occurrenceQuery.data]);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [eventType, setEventType] = useState("lesson");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [locationName, setLocationName] = useState("");
  const [locationAddress, setLocationAddress] = useState("");
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [instructorIds, setInstructorIds] = useState<string[]>([]);
  const [instructorNames, setInstructorNames] = useState<Record<string, string>>({});
  const [instructorPickerOpen, setInstructorPickerOpen] = useState(false);
  const [hydratedFor, setHydratedFor] = useState<string | null>(null);

  const formKey = mode.kind === "create" ? "create" : mode.kind === "edit-event" ? mode.eventId : mode.occurrenceId;

  // Hydrates local form state from the loaded Event/Occurrence exactly once
  // per opened dialog (`hydratedFor` guards against the query refetching —
  // e.g. after a save elsewhere — and clobbering in-progress edits).
  useEffect(() => {
    if (!initial || hydratedFor === formKey) return;
    setTitle(initial.title);
    setDescription(initial.description);
    setEventType(initial.eventType);
    setStart(initial.start);
    setEnd(initial.end);
    setLocationName(initial.locationName);
    setLocationAddress(initial.locationAddress);
    setGroupIds(initial.groupIds);
    setInstructorIds(initial.instructorIds);
    setHydratedFor(formKey);
  }, [initial, hydratedFor, formKey]);

  // Caches every instructor name this dialog has ever seen from the
  // Directory (both this unfiltered prefetch and the picker's own search
  // results below merge in here), so a chip never shows a bare id once
  // its owner has appeared in a directory response.
  useEffect(() => {
    if (!instructorDirectoryQuery.data) return;
    setInstructorNames((previous) => {
      const next = { ...previous };
      for (const user of instructorDirectoryQuery.data.items) next[user.id] = userFullName(user);
      return next;
    });
  }, [instructorDirectoryQuery.data]);

  function toggleGroup(groupId: string) {
    setGroupIds((previous) =>
      previous.includes(groupId) ? previous.filter((id) => id !== groupId) : [...previous, groupId],
    );
  }

  function toggleInstructor(user: { id: string; name: string }) {
    setInstructorNames((previous) => ({ ...previous, [user.id]: user.name }));
    setInstructorIds((previous) =>
      previous.includes(user.id) ? previous.filter((id) => id !== user.id) : [...previous, user.id],
    );
  }

  function removeInstructor(userId: string) {
    setInstructorIds((previous) => previous.filter((id) => id !== userId));
  }

  const isLocationEditable = mode.kind === "create" || mode.kind === "edit-event";
  const isValid =
    title.trim().length > 0 && start.length > 0 && end.length > 0 && new Date(start) < new Date(end);
  const isPending = createEvent.isPending || updateEvent.isPending || rescheduleOccurrence.isPending;

  function handleClose() {
    setHydratedFor(null);
    setInstructorPickerOpen(false);
    onClose();
  }

  function handleSubmit() {
    if (!isValid) return;
    const startAt = new Date(start).toISOString();
    const endAt = new Date(end).toISOString();

    if (mode.kind === "create") {
      const fields: EventFields = {
        club_id: mode.clubId,
        event_type: eventType,
        title: title.trim(),
        description: description.trim() || undefined,
        start_at: startAt,
        end_at: endAt,
        timezone: browserTimezone(),
        location_name: locationName.trim() || undefined,
        location_address: locationAddress.trim() || undefined,
        location_type: locationName.trim() ? "custom" : undefined,
        group_ids: groupIds,
        instructor_ids: instructorIds,
      };
      createEvent.mutate(fields, {
        onSuccess: () => {
          setHydratedFor(null);
          onSaved();
        },
        onError: (error) => notify("error", error.message),
      });
      return;
    }

    if (mode.kind === "edit-event") {
      updateEvent.mutate(
        {
          eventId: mode.eventId,
          fields: {
            event_type: eventType,
            title: title.trim(),
            description: description.trim() || undefined,
            start_at: startAt,
            end_at: endAt,
            timezone: browserTimezone(),
            location_name: locationName.trim() || undefined,
            location_address: locationAddress.trim() || undefined,
            location_type: locationName.trim() ? "custom" : undefined,
            group_ids: groupIds,
            instructor_ids: instructorIds,
          },
        },
        {
          onSuccess: () => {
            setHydratedFor(null);
            onSaved();
          },
          onError: (error) => notify("error", error.message),
        },
      );
      return;
    }

    // edit-occurrence: the only backend-supported occurrence-level mutation
    // for these fields (api/events.ts's useRescheduleOccurrence docstring).
    rescheduleOccurrence.mutate(
      {
        seriesId: mode.seriesId,
        occurrenceId: mode.occurrenceId,
        effective_start_at: startAt,
        effective_end_at: endAt,
        overrides: {
          name: title.trim(),
          description: description.trim() || undefined,
          event_type: eventType,
        },
      },
      {
        onSuccess: () => {
          setHydratedFor(null);
          onSaved();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  const title_ = mode.kind === "create" ? "Новое событие" : "Редактирование события";

  let body: ReactNode;
  if (loadingExisting) {
    body = <Loading label="Загружаем событие…" />;
  } else {
    body = (
      <div className={styles.form}>
        <Input label="Название" value={title} onChange={(event) => setTitle(event.target.value)} required />
        <Input
          label="Описание"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <FilterSelect label="Тип" value={eventType} options={TYPE_FILTER_OPTIONS.slice(1)} onChange={setEventType} />
        <Input
          label="Начало"
          type="datetime-local"
          value={start}
          onChange={(event) => setStart(event.target.value)}
          required
        />
        <Input
          label="Окончание"
          type="datetime-local"
          value={end}
          onChange={(event) => setEnd(event.target.value)}
          required
        />
        {isLocationEditable ? (
          <>
            <Input
              label="Место"
              value={locationName}
              onChange={(event) => setLocationName(event.target.value)}
            />
            <Input
              label="Адрес"
              value={locationAddress}
              onChange={(event) => setLocationAddress(event.target.value)}
            />
          </>
        ) : null}
        {isTargetingEditable ? (
          <>
            <div className={styles.targetingField}>
              <span className={styles.targetingLabel}>
                Группы
                {groupIds.length === 0 ? " — событие адресовано всем участникам клуба" : ""}
              </span>
              {groupsQuery.isLoading ? <Loading label="Загружаем группы…" /> : null}
              {groupsQuery.isError ? (
                <ErrorState
                  illustration="error"
                  title="Не удалось загрузить группы"
                  description={groupsQuery.error.message}
                  action={
                    <Button variant="secondary" onClick={() => groupsQuery.refetch()}>
                      Повторить
                    </Button>
                  }
                />
              ) : null}
              {groupsQuery.isSuccess ? (
                <ul className={styles.pickerList}>
                  {groupsQuery.data.items.map((group) => (
                    <li key={group.id}>
                      <label className={`${styles.pickerItem} ${styles.pickerItemRow}`}>
                        <input
                          type="checkbox"
                          checked={groupIds.includes(group.id)}
                          onChange={() => toggleGroup(group.id)}
                        />
                        {group.name}
                      </label>
                    </li>
                  ))}
                  {groupsQuery.data.items.length === 0 ? (
                    <li className={styles.pickerEmpty}>Нет активных групп</li>
                  ) : null}
                </ul>
              ) : null}
            </div>

            <div className={styles.targetingField}>
              <span className={styles.targetingLabel}>Ответственные инструкторы</span>
              {instructorIds.length > 0 ? (
                <div className={styles.chipList}>
                  {instructorIds.map((id) => (
                    <span key={id} className={styles.chipRemovable}>
                      {instructorNames[id] ?? id}
                      <button
                        type="button"
                        className={styles.chipRemoveButton}
                        aria-label={`Убрать ${instructorNames[id] ?? "инструктора"}`}
                        onClick={() => removeInstructor(id)}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              <Button variant="secondary" onClick={() => setInstructorPickerOpen(true)}>
                Добавить инструктора
              </Button>
            </div>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <>
      <Dialog
        open={open}
        title={title_}
        onClose={handleClose}
        actions={
          <>
            <Button variant="secondary" onClick={handleClose} disabled={isPending}>
              Отмена
            </Button>
            <Button variant="primary" onClick={handleSubmit} disabled={!isValid || isPending || loadingExisting}>
              {mode.kind === "create" ? "Создать" : "Сохранить"}
            </Button>
          </>
        }
      >
        {body}
      </Dialog>
      {isTargetingEditable ? (
        <InstructorMultiPickerDialog
          open={instructorPickerOpen}
          clubId={targetingClubId}
          selectedIds={instructorIds}
          onToggle={toggleInstructor}
          onClose={() => setInstructorPickerOpen(false)}
        />
      ) : null}
    </>
  );
}

function InstructorMultiPickerDialog({
  open,
  clubId,
  selectedIds,
  onToggle,
  onClose,
}: {
  open: boolean;
  clubId: string | null;
  selectedIds: string[];
  onToggle: (user: { id: string; name: string }) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const usersQuery = useUsers({
    role: "instructor",
    club_id: clubId ?? undefined,
    search: debouncedSearch,
    enabled: open,
  });

  function handleClose() {
    setSearch("");
    onClose();
  }

  return (
    <Dialog
      open={open}
      title="Выбрать инструкторов"
      onClose={handleClose}
      actions={
        <Button variant="primary" onClick={handleClose}>
          Готово
        </Button>
      }
    >
      <div className={styles.form}>
        <SearchInput
          label="Поиск инструктора"
          value={search}
          onChange={setSearch}
          placeholder="Например, «Иванова»"
        />
        {usersQuery.isLoading ? <Loading label="Загружаем инструкторов…" /> : null}
        {usersQuery.isError ? (
          <ErrorState
            illustration="error"
            title="Не удалось загрузить инструкторов"
            description={usersQuery.error.message}
            action={
              <Button variant="secondary" onClick={() => usersQuery.refetch()}>
                Повторить
              </Button>
            }
          />
        ) : null}
        {usersQuery.isSuccess ? (
          <ul className={styles.pickerList}>
            {usersQuery.data.items.map((user) => (
              <li key={user.id}>
                <label className={`${styles.pickerItem} ${styles.pickerItemRow}`}>
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(user.id)}
                    onChange={() => onToggle({ id: user.id, name: userFullName(user) })}
                  />
                  {userFullName(user)}
                </label>
              </li>
            ))}
            {usersQuery.data.items.length === 0 ? (
              <li className={styles.pickerEmpty}>Ничего не найдено</li>
            ) : null}
          </ul>
        ) : null}
      </div>
    </Dialog>
  );
}
