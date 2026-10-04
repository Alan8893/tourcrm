import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";

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
import {
  useCreateEventDocumentRequirement,
  useDeleteEventDocumentRequirement,
  useEventDocumentReadinessMatrix,
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
  monthGridRange,
  parseDateParam,
  startOfDay,
  toDatetimeLocalValue,
  type MonthGridDay,
} from "../domain/calendarDate";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { hasAdministratorRole } from "../shell/navigation";
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

  // The whole visible grid, not just the month: the first displayed week's
  // previous-month dates (and trailing next-month dates) must show their
  // events too (IA §8.0 first-week visibility, Issue #212).
  const range = useMemo(() => monthGridRange(selectedDate), [selectedDate]);
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

  // TH-0120 / Issue #227: a linked Event opened from News arrives as
  // `?date=<its day>&event=<event id>` — the calendar lands on that day
  // (existing `date` URL state) and the Event's detail opens once it is
  // among the loaded, backend-authorized calendar items. Nothing opens
  // for an id the calendar does not return.
  const [searchParams] = useSearchParams();
  const deepLinkEventId = searchParams.get("event");
  const [openedDeepLinkId, setOpenedDeepLinkId] = useState<string | null>(null);
  useEffect(() => {
    if (!deepLinkEventId || openedDeepLinkId === deepLinkEventId) return;
    const match = calendarQuery.data?.find(
      (item) => item.kind === "event" && item.id === deepLinkEventId,
    );
    if (match) {
      setDetailItem(match);
      setOpenedDeepLinkId(deepLinkEventId);
    }
  }, [deepLinkEventId, openedDeepLinkId, calendarQuery.data]);
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
  // Participant Export (participant-export-api.md §1-§2, import-export-ui.md
  // §8) and the competition-documents workflow (Issue #175: documents are
  // maintained by the Administrator) are Administrator-only — a role check
  // is the documented visibility rule. Backend authorization stays
  // authoritative.
  const meQuery = useCurrentUser();
  const isAdmin = hasAdministratorRole(meQuery.data?.role_assignments ?? []);

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
        {/* Issue #175: documents are maintained by the Administrator only
            (document.read/manage/export are granted to `admin` alone);
            same visibility convention as «Экспорт участников» below.
            The backend stays the authorization boundary. */}
        {item.kind === "event" && isAdmin ? (
          <Button variant="secondary" onClick={() => setDocumentsOpen(true)}>
            Документы для соревнования
          </Button>
        ) : null}
        {/* TH-0118.5 contextual action (import-export-ui.md §5.2): opens
            the Export master with this Event pre-selected; the Group +
            Event variant is chosen there and resolved by the backend.
            Only a real Event (not a recurring occurrence) is an export
            target (`event_id`, participant-export-api.md §3). */}
        {item.kind === "event" && isAdmin ? (
          <Link to={`/reports/export?context=event&event_id=${item.id}`}>
            <Button variant="secondary" icon="action.download">
              Экспорт участников
            </Button>
          </Link>
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
// «Документы для соревнования» — the Event-scoped workflow: managing
// EventDocumentRequirement rows, the participant x requirement readiness
// matrix (one `GET .../document-requirements/matrix` request,
// events-api.md §31.4) and the competition package export. Every
// readiness value, the participant set and the package contents are
// backend-derived; nothing here computes validity, readiness or
// duplicates. Documents themselves are corrected only in the existing
// Person → Документы tab, reached from a participant row.

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
        <ParticipantReadinessSection eventId={eventId} />
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

      <AddRequirementDialog open={addOpen} eventId={eventId} onClose={() => setAddOpen(false)} />
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
  onClose,
}: {
  open: boolean;
  eventId: string;
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

  function handleSubmit() {
    if (!documentType.trim()) return;
    createRequirement.mutate(
      { eventId, document_type: documentType.trim(), required },
      {
        onSuccess: () => {
          notify("success", "Требование добавлено");
          handleClose();
        },
        // The backend is the only duplicate check (409
        // `duplicate_document_requirement`, events-api.md §31.1); the
        // dialog stays open so the type can be corrected.
        onError: (error) =>
          notify(
            "error",
            error.status === 409 && error.code === "duplicate_document_requirement"
              ? "Требование для этого типа документа уже существует"
              : error.message,
          ),
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
            disabled={!documentType.trim() || createRequirement.isPending}
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
          hint={`«medical_certificate» отображается как «${documentTypeLabel("medical_certificate")}»`}
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

function ParticipantReadinessSection({ eventId }: { eventId: string }) {
  const matrixQuery = useEventDocumentReadinessMatrix(eventId);
  const [, setSearchParams] = useSearchParams();
  const headingId = useId();

  // Before leaving for Person → Документы, record this Event in the
  // calendar's existing `?event=` deep link (replace, not push) so the
  // browser Back button reopens the Event; the matrix refetches on return.
  function rememberEvent() {
    setSearchParams(
      (params) => {
        const next = new URLSearchParams(params);
        next.set("event", eventId);
        return next;
      },
      { replace: true },
    );
  }

  const participants = matrixQuery.data?.participants ?? [];
  const requirementCount = participants[0]?.requirements.length ?? 0;

  return (
    <section aria-labelledby={headingId}>
      <div className={styles.documentSectionHeader}>
        <h3 id={headingId}>Готовность участников</h3>
      </div>

      {matrixQuery.isLoading ? <Loading label="Загружаем готовность участников…" /> : null}
      {matrixQuery.isError ? (
        <ErrorState
          illustration={
            matrixQuery.error.status === 403 ? "403" : matrixQuery.error.status === 404 ? "404" : "error"
          }
          title={
            matrixQuery.error.status === 403
              ? "Нет доступа к документам участников"
              : matrixQuery.error.status === 404
                ? "Мероприятие не найдено"
                : "Не удалось загрузить готовность участников"
          }
          description={matrixQuery.error.message}
          action={
            matrixQuery.error.status !== 403 && matrixQuery.error.status !== 404 ? (
              <Button variant="secondary" onClick={() => void matrixQuery.refetch()}>
                Повторить
              </Button>
            ) : undefined
          }
        />
      ) : null}
      {matrixQuery.isSuccess && participants.length === 0 ? (
        <EmptyState illustration="no-results" title="Нет зарегистрированных участников" />
      ) : null}
      {matrixQuery.isSuccess && participants.length > 0 && requirementCount === 0 ? (
        <EmptyState illustration="no-results" title="Требования к документам не заданы" />
      ) : null}
      {matrixQuery.isSuccess && participants.length > 0 && requirementCount > 0 ? (
        <ul className={styles.documentList}>
          {participants.map((participant) => {
            const name = [participant.last_name, participant.first_name, participant.middle_name]
              .filter(Boolean)
              .join(" ");
            return (
              <li key={participant.person_id} className={styles.readinessRow}>
                <Link
                  to={`/people/${participant.person_id}?tab=documents`}
                  className={styles.readinessName}
                  onClick={rememberEvent}
                >
                  {name}
                </Link>
                <ul className={styles.readinessCells} aria-label={`Документы: ${name}`}>
                  {participant.requirements.map((check) => (
                    <li key={check.document_type} className={styles.readinessCell}>
                      <span className={styles.readinessType}>
                        {documentTypeLabel(check.document_type)}
                        {check.required ? null : (
                          <span className={styles.rowSecondary}> · Опционально</span>
                        )}
                      </span>
                      <StatusBadge
                        status={documentRequirementResultIcon(check.result)}
                        label={documentRequirementResultLabel(check.result)}
                      />
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
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
      <h3>Пакет документов</h3>
      <div className={styles.tabActions}>
        <Button
          variant="primary"
          icon="action.download"
          disabled={exportPackage.isPending}
          onClick={() => runExport(false)}
        >
          {exportPackage.isPending ? "Формирование…" : "Сформировать пакет документов"}
        </Button>
      </div>

      <Dialog
        open={incomplete !== null}
        title="Не все документы готовы"
        description={
          incomplete && incomplete.length > 0
            ? `Требуют внимания: ${incomplete.length}. Отсутствующие и истёкшие документы не попадут в пакет.`
            : "Отсутствующие и истёкшие документы не попадут в пакет."
        }
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
