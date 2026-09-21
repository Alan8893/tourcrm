import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";

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

export type LoginRequest = {
  identifier: string;
  password: string;
};

export type LoginResponse = {
  user: CurrentUser;
};

/** `POST /api/v1/auth/login` (TH-0090 / ADR-0027's own login-UI counterpart,
 * AUTH-LOGIN-UX-SPEC.md §2). On success, invalidates the cached `/auth/me`
 * query so the app re-derives the authenticated principal from the backend
 * (spec step 4) rather than trusting this response's own partial `user`
 * shape as if it were `/me` (which additionally carries `role_assignments`,
 * never returned by login). This is the *only* place besides `/auth/me`
 * that establishes identity — no client-side token/identity store is
 * created (spec §14). */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation<LoginResponse, ApiError, LoginRequest>({
    mutationFn: (payload) =>
      apiFetch<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export type PasswordResetRequestPayload = {
  identifier: string;
};

/** `POST /api/v1/auth/password-reset/request` — always resolves the same
 * way regardless of whether `identifier` resolves to a real account
 * (auth-api.md §13); the caller must not infer account existence from
 * this ever failing differently. */
export function usePasswordResetRequest() {
  return useMutation<void, ApiError, PasswordResetRequestPayload>({
    mutationFn: (payload) =>
      apiFetch<void>("/auth/password-reset/request", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export type PasswordResetConfirmPayload = {
  token: string;
  new_password: string;
};

/** `POST /api/v1/auth/password-reset/confirm` — the ONE canonical
 * mechanism for both self-service password recovery and ADR-0038's
 * admin-issued first-access/reset setup: the backend has no way to tell
 * the two apart (and does not need to), so this single hook backs both
 * the "Сброс пароля" and "Установите пароль" UI labels in
 * PasswordResetRequestPage — no separate `/first-access`/`/set-password`
 * flow exists or is introduced here. */
export function usePasswordResetConfirm() {
  return useMutation<void, ApiError, PasswordResetConfirmPayload>({
    mutationFn: (payload) =>
      apiFetch<void>("/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

/** TourCRM serves one club per deployment (business-rules.md §2.1); the
 * frontend has no `/clubs` listing endpoint, so the current club is
 * derived from the signed-in user's own role assignments rather than
 * invented or hardcoded. Returns null when unauthenticated or when every
 * assignment held is global (`club_id = null`). */
export function currentClubId(me: MeResponse | undefined): string | null {
  return me?.role_assignments.find((assignment) => assignment.club_id)?.club_id ?? null;
}
