import { formatDateParam } from "./calendarDate";

/** Publication date ("12 октября 2026"). */
export function formatNewsDate(instant: string): string {
  return new Date(instant).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** `event_date` is a calendar date (`YYYY-MM-DD`) — formatted from its
 * parts so no timezone conversion can shift it by a day. */
export function formatEventDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** Link into the Events calendar on the linked Event's day with that
 * Event opened (the Events page's own URL state: `date` + `event`). */
export function eventNavigationPath(event: { id: string; start_at: string }): string {
  const date = formatDateParam(new Date(event.start_at));
  return `/events?date=${date}&event=${encodeURIComponent(event.id)}`;
}
