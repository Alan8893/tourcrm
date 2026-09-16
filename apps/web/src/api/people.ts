import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";
import type { GuardianRelationshipStatus, MembershipStatus } from "../domain/statusMapping";

/**
 * People Core API client (TH-0094). Endpoints and response shapes match
 * the existing, unmodified contract in docs/05-api/people-api.md
 * §4-5/§13/§18 and the real routers (`app/api/v1/persons.py`) — no field
 * or endpoint is invented here.
 */

export type Person = {
  id: string;
  first_name: string;
  last_name: string;
  // `phone`/`email`/`address` are deliberately absent: ADR-0025 §8
  // withholds them from the baseline Person API for every requester,
  // including one holding `person.read` — not a client omission.
  middle_name: string | null;
  birth_date: string | null;
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

export type GuardianRelationship = {
  id: string;
  guardian_person_id: string;
  child_person_id: string;
  relationship_type: string;
  status: GuardianRelationshipStatus;
  is_primary_contact: boolean;
  valid_from: string;
  valid_to: string | null;
  created_at: string;
  updated_at: string;
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
