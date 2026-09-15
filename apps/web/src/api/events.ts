import { useQuery } from "@tanstack/react-query";

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
