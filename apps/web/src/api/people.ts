import { useQuery } from "@tanstack/react-query";

import { apiFetch, ApiError } from "./client";

export type Membership = {
  id: string;
  club_id: string;
  person_id: string;
  membership_type: string;
  status: string;
};

export type Person = {
  id: string;
  first_name: string;
  last_name: string;
  middle_name: string | null;
};

function useMembership(membershipId: string) {
  return useQuery<Membership, ApiError>({
    queryKey: ["memberships", "detail", membershipId],
    queryFn: () => apiFetch<Membership>(`/memberships/${membershipId}`),
  });
}

function usePerson(personId: string | undefined) {
  return useQuery<Person, ApiError>({
    queryKey: ["persons", "detail", personId],
    queryFn: () => apiFetch<Person>(`/persons/${personId}`),
    enabled: Boolean(personId),
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
    name: person.data
      ? [person.data.last_name, person.data.first_name].filter(Boolean).join(" ")
      : null,
  };
}
