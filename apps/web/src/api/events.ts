import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";
import type { EventStatus } from "../domain/statusMapping";

export type EventSummary = {
  id: string;
  club_id: string;
  event_type: string;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
  timezone: string;
  status: EventStatus;
};

/** Home's "nearest events" widget: the next few upcoming, published
 * events the signed-in user can already see — authorization/scope
 * filtering happens entirely on the backend (GET /api/v1/events), the
 * frontend applies no additional visibility logic of its own. */
export function useUpcomingEvents(limit = 5) {
  return useQuery<CollectionResponse<EventSummary>, ApiError>({
    queryKey: ["events", "upcoming", limit],
    queryFn: () =>
      apiFetch<CollectionResponse<EventSummary>>(
        `/events?status=published&sort=start_at&page_size=${limit}`,
      ),
  });
}

// --- Calendar (GET /api/v1/events/calendar, events-api.md §16) -------------

/** One `GET /events/calendar` row — either an ordinary `Event` (`kind:
 * "event"`) or a materialized recurring `EventOccurrence` (`kind:
 * "occurrence"`). Field set matches the backend's `CalendarItemOut`
 * exactly (app/api/v1/events_schemas.py) — group/instructor/location are
 * deliberately absent because the backend calendar projection does not
 * expose them (see the frontend PR description for the full mismatch
 * note); this type must not invent fields the API does not return. */
export type CalendarItem = {
  id: string;
  kind: "event" | "occurrence";
  club_id: string;
  event_type: string;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
  timezone: string;
  status: string;
  cancellation_reason: string | null;
  /** Populated only for `kind: "occurrence"`. */
  series_id: string | null;
  series_version: number | null;
};

export type CalendarFilters = {
  group_id?: string;
  user_id?: string;
  event_type?: string;
  status?: string;
};

export type CalendarRangeParams = CalendarFilters & {
  from: string;
  to: string;
};

const CALENDAR_PAGE_SIZE = 100;

function calendarQueryString(params: CalendarRangeParams, page: number): string {
  const query = new URLSearchParams({
    from: params.from,
    to: params.to,
    page: String(page),
    page_size: String(CALENDAR_PAGE_SIZE),
  });
  if (params.group_id) query.set("group_id", params.group_id);
  if (params.user_id) query.set("user_id", params.user_id);
  if (params.event_type) query.set("event_type", params.event_type);
  if (params.status) query.set("status", params.status);
  return query.toString();
}

/** Fetches every page of the requested `[from,to)` calendar range and
 * merges them before returning — ADR-0036 / events-api.md §16: "if a
 * range spans multiple pages, sequentially load all pages before building
 * the visual calendar." No separate pagination UI is ever shown inside
 * the calendar; this is the entire pagination contract. */
async function fetchFullCalendarRange(params: CalendarRangeParams): Promise<CalendarItem[]> {
  const first = await apiFetch<CollectionResponse<CalendarItem>>(
    `/events/calendar?${calendarQueryString(params, 1)}`,
  );
  const items = [...first.items];
  for (let page = 2; page <= first.pagination.pages; page += 1) {
    const next = await apiFetch<CollectionResponse<CalendarItem>>(
      `/events/calendar?${calendarQueryString(params, page)}`,
    );
    items.push(...next.items);
  }
  return items;
}

export function useCalendarRange(params: CalendarRangeParams) {
  return useQuery<CalendarItem[], ApiError>({
    queryKey: [
      "events",
      "calendar",
      params.from,
      params.to,
      params.group_id ?? "",
      params.user_id ?? "",
      params.event_type ?? "",
      params.status ?? "",
    ],
    queryFn: () => fetchFullCalendarRange(params),
    // Keeps the previous range's items on screen while a new range loads
    // (ADR-0036: "period navigation keeps the previous content visible")
    // instead of an abrupt data -> empty -> loading -> data flash.
    placeholderData: (previous) => previous,
  });
}

// --- Event detail / create / update (GET|POST|PATCH /api/v1/events) --------

export type EventDetail = {
  id: string;
  club_id: string;
  event_type: string;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
  timezone: string;
  location_type: string | null;
  location_name: string | null;
  location_address: string | null;
  location_latitude: number | null;
  location_longitude: number | null;
  status: string;
  cancellation_reason: string | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
};

export function useEvent(eventId: string | undefined) {
  return useQuery<EventDetail, ApiError>({
    queryKey: ["events", "detail", eventId],
    queryFn: () => apiFetch<EventDetail>(`/events/${eventId}`),
    enabled: Boolean(eventId),
  });
}

export type EventFields = {
  club_id: string;
  event_type: string;
  title: string;
  description?: string;
  start_at: string;
  end_at: string;
  timezone: string;
  location_type?: string;
  location_name?: string;
  location_address?: string;
  location_latitude?: number;
  location_longitude?: number;
};

function invalidateCalendarAndEvent(queryClient: ReturnType<typeof useQueryClient>, eventId?: string) {
  void queryClient.invalidateQueries({ queryKey: ["events", "calendar"] });
  void queryClient.invalidateQueries({ queryKey: ["events", "upcoming"] });
  if (eventId) void queryClient.invalidateQueries({ queryKey: ["events", "detail", eventId] });
}

export function useCreateEvent() {
  const queryClient = useQueryClient();
  return useMutation<EventDetail, ApiError, EventFields>({
    mutationFn: (input) =>
      apiFetch<EventDetail>("/events", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => invalidateCalendarAndEvent(queryClient),
  });
}

export type EventUpdateFields = Partial<Omit<EventFields, "club_id">>;

export function useUpdateEvent() {
  const queryClient = useQueryClient();
  return useMutation<EventDetail, ApiError, { eventId: string; fields: EventUpdateFields }>({
    mutationFn: ({ eventId, fields }) =>
      apiFetch<EventDetail>(`/events/${eventId}`, {
        method: "PATCH",
        body: JSON.stringify(fields),
      }),
    onSuccess: (event) => invalidateCalendarAndEvent(queryClient, event.id),
  });
}

// --- Occurrence detail / reschedule (events-api.md §16, event-recurrence-api.md) -

export type OccurrenceDetail = {
  id: string;
  series_id: string | null;
  club_id: string;
  name: string;
  description: string | null;
  event_type: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  status: string;
  cancellation_reason: string | null;
};

export function useOccurrence(occurrenceId: string | undefined) {
  return useQuery<OccurrenceDetail, ApiError>({
    queryKey: ["events", "occurrence", occurrenceId],
    queryFn: () => apiFetch<OccurrenceDetail>(`/events/occurrences/${occurrenceId}`),
    enabled: Boolean(occurrenceId),
  });
}

/** Edits a single recurring occurrence through the *only* backend-
 * supported occurrence-level mutation for name/description/type/time
 * (ADR-0028 §5 / event-recurrence-api.md): `POST /events/series/
 * {series_id}/exceptions` with `exception_type: "rescheduled"`. This is
 * deliberately narrower than an ordinary Event edit — occurrences have no
 * location field and `overrides` is server-allow-listed to `name`/
 * `description`/`event_type` only (app.events.series_vocabulary.
 * ALLOWED_OCCURRENCE_OVERRIDE_FIELDS) — the form must never offer more
 * than this. There is no frontend recurrence engine or scope picker: this
 * always applies to exactly the one selected occurrence, the only scope
 * this endpoint has ever supported. */
export type OccurrenceRescheduleFields = {
  seriesId: string;
  occurrenceId: string;
  effective_start_at: string;
  effective_end_at: string;
  overrides: {
    name: string;
    description?: string;
    event_type: string;
  };
};

export function useRescheduleOccurrence() {
  const queryClient = useQueryClient();
  return useMutation<OccurrenceDetail, ApiError, OccurrenceRescheduleFields>({
    mutationFn: ({ seriesId, occurrenceId, effective_start_at, effective_end_at, overrides }) =>
      apiFetch<OccurrenceDetail>(`/events/series/${seriesId}/exceptions`, {
        method: "POST",
        body: JSON.stringify({
          occurrence_id: occurrenceId,
          exception_type: "rescheduled",
          effective_start_at,
          effective_end_at,
          overrides,
        }),
      }),
    onSuccess: (occurrence) => {
      void queryClient.invalidateQueries({ queryKey: ["events", "calendar"] });
      void queryClient.invalidateQueries({ queryKey: ["events", "occurrence", occurrence.id] });
    },
  });
}
