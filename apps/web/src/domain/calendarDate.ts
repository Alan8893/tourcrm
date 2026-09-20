/**
 * Pure calendar date/range logic for the Events calendar (TH-0105 /
 * ADR-0036). No React, no fetching — everything here is deterministic and
 * unit-testable in isolation.
 *
 * All "local" dates are browser-timezone wall-clock dates (ADR-0036: no
 * persistent user timezone setting, display uses the browser timezone).
 * JS `Date` month/day arithmetic (`setMonth`/`setDate`/...) already
 * accounts for DST transitions because it operates on local wall-clock
 * fields, not raw millisecond addition — so no separate DST handling is
 * needed here.
 */

const DATE_PARAM_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** `YYYY-MM-DD` in the *browser's local* calendar — never `toISOString()`
 * (which is UTC and can land on the wrong local day near midnight). */
export function formatDateParam(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Parses a `date` query param as a local calendar day. Returns `null` for
 * anything missing, malformed, or not a real calendar date (e.g.
 * `2026-02-30`) — callers fall back to "today" rather than trusting an
 * unvalidated URL value. */
export function parseDateParam(value: string | null): Date | null {
  if (!value || !DATE_PARAM_PATTERN.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  const isRealDate =
    date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
  return isRealDate ? date : null;
}

export function startOfDay(date: Date): Date {
  const result = new Date(date);
  result.setHours(0, 0, 0, 0);
  return result;
}

export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  );
}

export function addMonths(date: Date, delta: number): Date {
  const result = new Date(date);
  const day = result.getDate();
  result.setDate(1);
  result.setMonth(result.getMonth() + delta);
  // Clamp to the target month's last day instead of overflowing into the
  // month after (e.g. Jan 31 + 1 month must land on Feb 28/29, not Mar 3).
  const lastDayOfTargetMonth = new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate();
  result.setDate(Math.min(day, lastDayOfTargetMonth));
  return result;
}

export function addDays(date: Date, delta: number): Date {
  const result = new Date(date);
  result.setDate(result.getDate() + delta);
  return result;
}

export type MonthRange = {
  /** RFC 3339 instant, local midnight of the 1st of the month — inclusive. */
  from: string;
  /** RFC 3339 instant, local midnight of the 1st of the following month — exclusive. */
  to: string;
};

/** The calendar's `[from,to)` request range for the month containing
 * `date` (events-api.md §16 — bounds are timezone-aware RFC 3339 instants;
 * the browser converts its own local month boundary to the correct UTC
 * instant via `toISOString()`). */
export function monthRange(date: Date): MonthRange {
  const from = new Date(date.getFullYear(), date.getMonth(), 1);
  const to = new Date(date.getFullYear(), date.getMonth() + 1, 1);
  return { from: from.toISOString(), to: to.toISOString() };
}

export type MonthGridDay = {
  date: Date;
  key: string;
  inCurrentMonth: boolean;
  isToday: boolean;
};

const WEEKDAY_COUNT = 7;
const GRID_WEEKS = 6;

/** A fixed 6-week (42-day) Monday-start grid covering the month containing
 * `date`, padded with the trailing days of the previous month and the
 * leading days of the next — the standard month-calendar layout. */
export function buildMonthGrid(date: Date, today: Date = new Date()): MonthGridDay[] {
  const firstOfMonth = new Date(date.getFullYear(), date.getMonth(), 1);
  // JS getDay(): 0=Sunday..6=Saturday. Convert to a Monday-start offset.
  const mondayIndex = (firstOfMonth.getDay() + 6) % 7;
  const gridStart = addDays(firstOfMonth, -mondayIndex);

  return Array.from({ length: GRID_WEEKS * WEEKDAY_COUNT }, (_, index) => {
    const cellDate = addDays(gridStart, index);
    return {
      date: cellDate,
      key: formatDateParam(cellDate),
      inCurrentMonth: cellDate.getMonth() === date.getMonth(),
      isToday: isSameDay(cellDate, today),
    };
  });
}

export const WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;

const MONTH_LABELS = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
] as const;

export function monthLabel(date: Date): string {
  return `${MONTH_LABELS[date.getMonth()]} ${date.getFullYear()}`;
}

/** `YYYY-MM-DDTHH:mm` in the browser's local time, the value shape
 * `<input type="datetime-local">` requires. The reverse direction needs no
 * helper: the native `Date` constructor already parses that exact shape
 * as browser-local time (never UTC), so `new Date(value)` is enough to go
 * back — this stays a one-way helper deliberately, not a matched pair of
 * hand-rolled parsers. */
export function toDatetimeLocalValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

/** Browser-timezone `HH:MM`. */
export function formatTime(instant: string): string {
  return new Date(instant).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

/** Browser-timezone short date, e.g. "17 марта". */
export function formatDayMonth(instant: string): string {
  return new Date(instant).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}
