import { useQuery } from "@tanstack/react-query";

import { apiFetch, ApiError } from "./client";

export type PersonSummary = {
  first_name: string;
  last_name: string;
  middle_name: string | null;
  birth_date: string | null;
};

export type CurrentUser = {
  id: string;
  login_identifier: string;
  status: string;
  email_verified_at: string | null;
  person: PersonSummary;
};

export type RoleAssignmentSummary = {
  role_code: string;
  club_id: string | null;
  scope_type: string;
};

export type MeResponse = {
  user: CurrentUser;
  role_assignments: RoleAssignmentSummary[];
};

/** `GET /api/v1/auth/me`. A 401 (no session yet — this Issue does not add
 * a login screen) is treated as "signed out", not a hard failure: callers
 * check `isError` and render a guest state rather than surfacing a system
 * error page for the expected unauthenticated case. */
export function useCurrentUser() {
  return useQuery<MeResponse, ApiError>({
    queryKey: ["auth", "me"],
    queryFn: () => apiFetch<MeResponse>("/auth/me"),
    retry: false,
  });
}

export function displayName(user: CurrentUser): string {
  return [user.person.last_name, user.person.first_name].filter(Boolean).join(" ");
}

/** TourCRM serves one club per deployment (business-rules.md §2.1); the
 * frontend has no `/clubs` listing endpoint, so the current club is
 * derived from the signed-in user's own role assignments rather than
 * invented or hardcoded. Returns null when unauthenticated or when every
 * assignment held is global (`club_id = null`). */
export function currentClubId(me: MeResponse | undefined): string | null {
  return me?.role_assignments.find((assignment) => assignment.club_id)?.club_id ?? null;
}
