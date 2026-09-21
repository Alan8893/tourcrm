import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";
import type { GuardianRelationshipStatus, MembershipStatus } from "../domain/statusMapping";

/**
 * People API client (TH-0094 read endpoints, TH-0104 mutations). Endpoints
 * and response shapes match the real routers (`app/api/v1/persons.py`,
 * `memberships.py`, `guardian_relationships.py`, `me.py`) and
 * docs/05-api/people-api.md as reconciled to ADR-0035 — no field or
 * endpoint is invented here.
 */

export type Person = {
  id: string;
  first_name: string;
  last_name: string;
  middle_name: string | null;
  birth_date: string | null;
  // `phone`/`email`/`address`/`photo_file_id` ARE part of `PersonOut` for
  // an authorized viewer (ADR-0035 §4, people-api.md §5) — an earlier
  // version of this type omitted them citing a stale ADR-0025 reading;
  // that was wrong and blocked Person edit (TH-0104) outright.
  phone: string | null;
  email: string | null;
  address: string | null;
  photo_file_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Membership = {
  id: string;
  club_id: string;
  person_id: string;
  membership_type: string;
  status: MembershipStatus;
  joined_at: string;
  left_at: string | null;
  created_at: string;
  updated_at: string;
};

// ADR-0035 §7.2 / app/people/lifecycle.py's ALLOWED_STATUS_TRANSITIONS,
// reproduced verbatim as a pure UX convenience for populating the
// status-change picker with only the currently valid next states. This is
// never a security boundary — the backend re-validates every transition
// independently and is the sole source of truth; a stale/incorrect copy
// here can only make the UI *less* permissive than the backend, never
// more, since the actual POST .../status call is still checked server-side.
export const MEMBERSHIP_NEXT_STATUSES: Record<MembershipStatus, MembershipStatus[]> = {
  pending: ["active", "archived"],
  active: ["suspended", "inactive", "archived"],
  suspended: ["active", "inactive", "archived"],
  inactive: ["archived"],
  archived: [],
};

export const CANONICAL_MEMBERSHIP_STATUSES: MembershipStatus[] = [
  "pending",
  "active",
  "suspended",
  "inactive",
  "archived",
];

export type GuardianRelationship = {
  id: string;
  guardian_person_id: string;
  child_person_id: string;
  relationship_type: string;
  status: GuardianRelationshipStatus;
  // TH-0103 (PR #126) removed `is_primary_contact` from the backend
  // entirely — ADR-0035 §8: no primary/priority concept exists for
  // GuardianRelationship. Do not reintroduce it here.
  valid_from: string;
  valid_to: string | null;
  created_at: string;
  updated_at: string;
};

/** `GET /me/children` projection (ADR-0035 §9) — exactly these 6 fields,
 * never enriched with a `usePerson(child.id)` call: no contacts, no other
 * GuardianRelationship data. */
export type Child = {
  id: string;
  last_name: string;
  first_name: string;
  middle_name: string | null;
  birth_date: string | null;
  photo_file_id: string | null;
};

export function personFullName(person: {
  first_name: string;
  last_name: string;
  middle_name: string | null;
}): string {
  return [person.last_name, person.first_name, person.middle_name].filter(Boolean).join(" ");
}

const PEOPLE_LIST_PAGE_SIZE = 20;
// Sub-lists on a Person's own detail screen (their own memberships/
// guardians) are shown in full on one page — pagination controls are
// only required for the main People list (Issue #109 scope).
const PERSON_SUBLIST_PAGE_SIZE = 50;

/** `GET /api/v1/persons?page&page_size&search` (people-api.md §4,
 * app/api/v1/persons.py). `search` matches first/last name server-side
 * (app/people/queries.py) — never a client-side filter over one page. */
export function usePersons(params: { page: number; search: string }) {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(PEOPLE_LIST_PAGE_SIZE),
  });
  const trimmedSearch = params.search.trim();
  if (trimmedSearch) query.set("search", trimmedSearch);

  return useQuery<CollectionResponse<Person>, ApiError>({
    queryKey: ["persons", "list", params.page, trimmedSearch],
    queryFn: () => apiFetch<CollectionResponse<Person>>(`/persons?${query.toString()}`),
    placeholderData: keepPreviousData,
  });
}

/** `GET /api/v1/persons/{person_id}` (people-api.md §5). Existence and
 * authorization are indistinguishable at the HTTP layer (404 either
 * way) — see `ApiError.status` handling in the pages that use this. */
export function usePerson(personId: string | undefined) {
  return useQuery<Person, ApiError>({
    queryKey: ["persons", "detail", personId],
    queryFn: () => apiFetch<Person>(`/persons/${personId}`),
    enabled: Boolean(personId),
  });
}

/** `GET /api/v1/persons/{person_id}/memberships` (people-api.md §13):
 * current and historical `ClubMembership` periods for this Person. */
export function usePersonMemberships(personId: string | undefined) {
  return useQuery<CollectionResponse<Membership>, ApiError>({
    queryKey: ["persons", "memberships", personId],
    queryFn: () =>
      apiFetch<CollectionResponse<Membership>>(
        `/persons/${personId}/memberships?page_size=${PERSON_SUBLIST_PAGE_SIZE}`,
      ),
    enabled: Boolean(personId),
  });
}

/** `GET /api/v1/persons/{person_id}/guardian-relationships`
 * (people-api.md §18/§20): the child-side view — this Person's own
 * guardians, never the reverse ("children of this person") direction,
 * which has no endpoint for an arbitrary `person_id` (only `/me/children`
 * for the authenticated principal). */
export function usePersonGuardianRelationships(personId: string | undefined) {
  return useQuery<CollectionResponse<GuardianRelationship>, ApiError>({
    queryKey: ["persons", "guardian-relationships", personId],
    queryFn: () =>
      apiFetch<CollectionResponse<GuardianRelationship>>(
        `/persons/${personId}/guardian-relationships?page_size=${PERSON_SUBLIST_PAGE_SIZE}`,
      ),
    enabled: Boolean(personId),
  });
}

export type MembershipRecord = {
  id: string;
  club_id: string;
  person_id: string;
};

function useMembership(membershipId: string) {
  return useQuery<MembershipRecord, ApiError>({
    queryKey: ["memberships", "detail", membershipId],
    queryFn: () => apiFetch<MembershipRecord>(`/memberships/${membershipId}`),
  });
}

/** A GroupMembership row only carries `club_membership_id`
 * (app/api/v1/groups_schemas.py) — there is no combined "membership with
 * person name" endpoint, so the display name for one row is resolved
 * through the two existing endpoints that do exist
 * (`/memberships/{id}` then `/persons/{id}`) rather than inventing a new
 * backend capability. */
export function useMembershipPersonName(clubMembershipId: string) {
  const membership = useMembership(clubMembershipId);
  const person = usePerson(membership.data?.person_id);
  return {
    isLoading: membership.isLoading || person.isLoading,
    isError: membership.isError || person.isError,
    name: person.data ? personFullName(person.data) : null,
  };
}

/** `GET /api/v1/me/children` (ADR-0035 §9): the authenticated Guardian's
 * own current children, safe projection only. Identity comes solely from
 * the session — no id parameter exists to request someone else's. */
export function useMyChildren() {
  return useQuery<CollectionResponse<Child>, ApiError>({
    queryKey: ["me", "children"],
    queryFn: () => apiFetch<CollectionResponse<Child>>("/me/children"),
  });
}

// --- Person mutations (TH-0101/TH-0104) ------------------------------------

export type PersonFields = {
  first_name?: string;
  last_name?: string;
  middle_name?: string | null;
  birth_date?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  photo_file_id?: string | null;
};

/** `POST /api/v1/persons` — `person.create`, admin-only, no `club_id` in
 * the request (Person itself stays Club-neutral). TH-0111 / Issue #140:
 * the backend now atomically creates the Person's initial active
 * ClubMembership for the current Club in the same transaction, so this
 * remains the single mutation the "Добавить человека" flow calls — never
 * followed by a separate `useCreateMembership()` call from the frontend. */
export function useCreatePerson() {
  const queryClient = useQueryClient();
  return useMutation<Person, ApiError, PersonFields>({
    mutationFn: (fields) => apiFetch<Person>("/persons", { method: "POST", body: JSON.stringify(fields) }),
    onSuccess: (person) => {
      queryClient.setQueryData(["persons", "detail", person.id], person);
      void queryClient.invalidateQueries({ queryKey: ["persons", "list"] });
    },
  });
}

/** `PATCH /api/v1/persons/{id}` — `person.update`. Send only the fields
 * that actually changed (PATCH/`exclude_unset` semantics mirrored
 * client-side): the caller is responsible for diffing against the loaded
 * `Person` before calling `mutate`. Sending `birth_date` requires the
 * caller to hold the system admin role (enforced server-side); this hook
 * does not gate that itself — the UI decides whether to offer the field
 * at all (see EditPersonDialog). */
export function useUpdatePerson() {
  const queryClient = useQueryClient();
  return useMutation<Person, ApiError, { personId: string; fields: PersonFields }>({
    mutationFn: ({ personId, fields }) =>
      apiFetch<Person>(`/persons/${personId}`, { method: "PATCH", body: JSON.stringify(fields) }),
    onSuccess: (person) => {
      queryClient.setQueryData(["persons", "detail", person.id], person);
      // Name changes affect how this Person renders in the list.
      void queryClient.invalidateQueries({ queryKey: ["persons", "list"] });
    },
  });
}

// --- ClubMembership mutations (TH-0102/TH-0104) ----------------------------

export type CreateMembershipInput = {
  person_id: string;
  club_id: string;
  membership_type: string;
  status: MembershipStatus;
  joined_at: string;
};

/** `POST /api/v1/memberships` — `membership.manage`, admin-only. Rejoining
 * after `inactive`/`archived` uses this same call again: it always
 * creates a brand-new period/row, never reactivates an old one (there is
 * no such transition). */
export function useCreateMembership() {
  const queryClient = useQueryClient();
  return useMutation<Membership, ApiError, CreateMembershipInput>({
    mutationFn: (input) =>
      apiFetch<Membership>("/memberships", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: (membership) => {
      void queryClient.invalidateQueries({
        queryKey: ["persons", "memberships", membership.person_id],
      });
    },
  });
}

/** `PATCH /api/v1/memberships/{id}` — `membership.manage`, admin-only.
 * Only `membership_type` is settable here; lifecycle status changes go
 * through `useTransitionMembershipStatus` exclusively. */
export function useUpdateMembershipType() {
  const queryClient = useQueryClient();
  return useMutation<
    Membership,
    ApiError,
    { membershipId: string; personId: string; membership_type: string }
  >({
    mutationFn: ({ membershipId, membership_type }) =>
      apiFetch<Membership>(`/memberships/${membershipId}`, {
        method: "PATCH",
        body: JSON.stringify({ membership_type }),
      }),
    onSuccess: (membership, { personId }) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "memberships", personId] });
      // Load-bearing: useMembershipPersonName (consumed by GroupDetailPage's
      // member list) reads this exact key — without this invalidation a
      // Group page could keep showing stale membership data after an
      // admin changes it here.
      void queryClient.invalidateQueries({ queryKey: ["memberships", "detail", membership.id] });
    },
  });
}

/** `POST /api/v1/memberships/{id}/status` — `membership.manage`,
 * admin-only. The only lifecycle-changing endpoint; see
 * `MEMBERSHIP_NEXT_STATUSES` for the allowed graph the UI mirrors. */
export function useTransitionMembershipStatus() {
  const queryClient = useQueryClient();
  return useMutation<
    Membership,
    ApiError,
    { membershipId: string; personId: string; status: MembershipStatus; reason?: string }
  >({
    mutationFn: ({ membershipId, status, reason }) =>
      apiFetch<Membership>(`/memberships/${membershipId}/status`, {
        method: "POST",
        body: JSON.stringify(reason ? { status, reason } : { status }),
      }),
    onSuccess: (membership, { personId }) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "memberships", personId] });
      void queryClient.invalidateQueries({ queryKey: ["memberships", "detail", membership.id] });
    },
  });
}

// --- GuardianRelationship mutations (TH-0103/TH-0104) ----------------------

/** `POST /api/v1/persons/{person_id}/guardian-relationships` —
 * `guardian_relationship.manage`, admin-only. `person_id` is the CHILD
 * side; `status` is always `"active"` (the only value the schema accepts
 * at create time) and is not client-configurable. */
export function useCreateGuardianRelationship() {
  const queryClient = useQueryClient();
  return useMutation<
    GuardianRelationship,
    ApiError,
    { personId: string; guardian_person_id: string; relationship_type: string }
  >({
    mutationFn: ({ personId, guardian_person_id, relationship_type }) =>
      apiFetch<GuardianRelationship>(`/persons/${personId}/guardian-relationships`, {
        method: "POST",
        body: JSON.stringify({ guardian_person_id, relationship_type, status: "active" }),
      }),
    onSuccess: (_relationship, { personId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["persons", "guardian-relationships", personId],
      });
    },
  });
}

/** `PATCH /api/v1/guardian-relationships/{id}` —
 * `guardian_relationship.manage`, admin-only. Only `relationship_type` is
 * settable — no primary/priority field exists (ADR-0035 §8). */
export function useUpdateGuardianRelationship() {
  const queryClient = useQueryClient();
  return useMutation<
    GuardianRelationship,
    ApiError,
    { relationshipId: string; personId: string; relationship_type: string }
  >({
    mutationFn: ({ relationshipId, relationship_type }) =>
      apiFetch<GuardianRelationship>(`/guardian-relationships/${relationshipId}`, {
        method: "PATCH",
        body: JSON.stringify({ relationship_type }),
      }),
    onSuccess: (_relationship, { personId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["persons", "guardian-relationships", personId],
      });
    },
  });
}

/** `POST /api/v1/guardian-relationships/{id}/terminate` —
 * `guardian_relationship.manage`, admin-only. Always sets
 * `status = "revoked"`, which is terminal — there is no restore
 * operation, and the UI must never offer one. */
export function useTerminateGuardianRelationship() {
  const queryClient = useQueryClient();
  return useMutation<GuardianRelationship, ApiError, { relationshipId: string; personId: string }>({
    mutationFn: ({ relationshipId }) =>
      apiFetch<GuardianRelationship>(`/guardian-relationships/${relationshipId}/terminate`, {
        method: "POST",
      }),
    onSuccess: (_relationship, { personId }) => {
      void queryClient.invalidateQueries({
        queryKey: ["persons", "guardian-relationships", personId],
      });
    },
  });
}

// --- System role assignments (TH-0112 / ADR-0039) --------------------------
//
// A Person-scoped, canonical-role-code-only view onto RoleAssignment
// (app/api/v1/persons.py's `.../role-assignments` endpoints). Identity
// (Person -> User) and scope are always resolved server-side — this
// client never sends a `user_id`, `role_id`, or `scope_type`, only one of
// the four canonical `role_code` values below.

export type PersonRoleCode = "admin" | "instructor" | "member" | "guardian";

export const CANONICAL_PERSON_ROLE_CODES: PersonRoleCode[] = [
  "admin",
  "instructor",
  "member",
  "guardian",
];

/** ADR-0039 §3's canonical human-readable labels — the People UI must
 * never render a raw role code, and must never use `membership_type` as
 * a substitute for these. */
export function personRoleLabel(roleCode: string): string {
  switch (roleCode) {
    case "admin":
      return "Администратор";
    case "instructor":
      return "Инструктор";
    case "member":
      return "Участник";
    case "guardian":
      return "Родитель";
    default:
      return roleCode;
  }
}

export type PersonRoleAssignment = {
  id: string;
  person_id: string;
  role_code: string;
  club_id: string | null;
  valid_from: string;
};

/** `GET /api/v1/persons/{person_id}/role-assignments` — `role.manage`.
 * Empty for a Person with no active roles, and (indistinguishably, at
 * this endpoint) for a Person with no linked User account yet — only
 * `useAddPersonRole`'s own rejection tells the two apart. */
export function usePersonRoleAssignments(personId: string | undefined) {
  return useQuery<CollectionResponse<PersonRoleAssignment>, ApiError>({
    queryKey: ["persons", "role-assignments", personId],
    queryFn: () =>
      apiFetch<CollectionResponse<PersonRoleAssignment>>(`/persons/${personId}/role-assignments`),
    enabled: Boolean(personId),
  });
}

/** `POST /api/v1/persons/{person_id}/role-assignments` — `role.manage`.
 * Rejects with `person_has_no_user_account` (422) when the Person has no
 * linked User, and `duplicate_role_assignment` (409) when the role is
 * already active — both are ordinary `ApiError`s for the caller to
 * surface, never silently retried or swallowed here. */
export function useAddPersonRole() {
  const queryClient = useQueryClient();
  return useMutation<
    PersonRoleAssignment,
    ApiError,
    { personId: string; role_code: PersonRoleCode }
  >({
    mutationFn: ({ personId, role_code }) =>
      apiFetch<PersonRoleAssignment>(`/persons/${personId}/role-assignments`, {
        method: "POST",
        body: JSON.stringify({ role_code }),
      }),
    onSuccess: (_assignment, { personId }) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "role-assignments", personId] });
    },
  });
}

/** `DELETE /api/v1/persons/{person_id}/role-assignments/{role_code}` —
 * `role.manage`. Removing a role that is not currently active (or a
 * Person with no User) is a 404 (`role_assignment_not_found`) —
 * existence-hiding, matching the top-level RoleAssignment API's own
 * revoke convention; the UI only ever offers this for a role it just
 * listed as active, so this should not occur in normal use. */
export function useRemovePersonRole() {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, { personId: string; role_code: PersonRoleCode }>({
    mutationFn: ({ personId, role_code }) =>
      apiFetch<void>(`/persons/${personId}/role-assignments/${role_code}`, { method: "DELETE" }),
    onSuccess: (_result, { personId }) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "role-assignments", personId] });
    },
  });
}

// --- Account management (TH-0113 / ADR-0038) --------------------------------
//
// Administrative User account/credential management for a Person, gated
// by `account.manage`. The server always resolves identity from the
// path (`person_id`) — this client never sends a `user_id`, `password`,
// or `role`. `temporary_credential` appears ONLY in the create/reset
// mutation responses below (ADR-0038 §3/§8) — never in `PersonAccount`
// itself, never persisted by this client, and never re-derivable after
// the response that carried it.

export type PersonAccount = {
  id: string;
  person_id: string;
  login_identifier: string;
  status: string;
  email_verified_at: string | null;
  last_login_at: string | null;
};

export type PersonAccountCredential = {
  account: PersonAccount;
  temporary_credential: string;
};

/** `GET /api/v1/persons/{person_id}/account` — `account.manage`. A 404
 * means "this Person has no User yet" (`account_not_found`) — the
 * caller renders that as the "no account" state, not as a system error;
 * see `ApiError.status` handling in the pages that use this. */
export function usePersonAccount(personId: string | undefined) {
  return useQuery<PersonAccount, ApiError>({
    queryKey: ["persons", "account", personId],
    queryFn: () => apiFetch<PersonAccount>(`/persons/${personId}/account`),
    enabled: Boolean(personId),
    retry: false,
  });
}

/** `POST /api/v1/persons/{person_id}/account` — `account.manage`. No
 * request body: `login_identifier` is always `Person.email` server-side.
 * Rejects with `person_email_missing` (422) when the Person has no
 * email, or `account_already_exists`/`duplicate_login_identifier` (409)
 * — ordinary `ApiError`s for the caller to surface. */
export function useCreatePersonAccount() {
  const queryClient = useQueryClient();
  return useMutation<PersonAccountCredential, ApiError, string>({
    mutationFn: (personId) =>
      apiFetch<PersonAccountCredential>(`/persons/${personId}/account`, { method: "POST" }),
    onSuccess: (_result, personId) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "account", personId] });
    },
  });
}

/** `POST /api/v1/persons/{person_id}/account/password-reset` —
 * `account.manage`. Issues a fresh one-time reset challenge for an
 * existing account; never creates a User and never accepts a new
 * password from the admin. Rejects with `person_has_no_account` (422)
 * when the Person has no User yet — use `useCreatePersonAccount`
 * first. */
export function useAdminResetPersonPassword() {
  const queryClient = useQueryClient();
  return useMutation<PersonAccountCredential, ApiError, string>({
    mutationFn: (personId) =>
      apiFetch<PersonAccountCredential>(`/persons/${personId}/account/password-reset`, {
        method: "POST",
      }),
    onSuccess: (_result, personId) => {
      void queryClient.invalidateQueries({ queryKey: ["persons", "account", personId] });
    },
  });
}
