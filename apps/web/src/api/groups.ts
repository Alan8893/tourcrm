import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";
import type { CalendarItem } from "./events";
import type { GroupStatus } from "../domain/statusMapping";

export type { CalendarItem };

export type Group = {
  id: string;
  club_id: string;
  name: string;
  description: string | null;
  status: GroupStatus;
  valid_from: string;
  valid_to: string | null;
  created_at: string;
  updated_at: string;
};

export type GroupMembership = {
  id: string;
  group_id: string;
  club_membership_id: string;
  valid_from: string;
  valid_to: string | null;
  membership_status: "active" | "ended";
  created_at: string;
  updated_at: string;
};

export function useGroups(params: { status?: GroupStatus } = {}) {
  const query = params.status ? `?status=${params.status}` : "";
  return useQuery<CollectionResponse<Group>, ApiError>({
    queryKey: ["groups", "list", params.status ?? "all"],
    queryFn: () => apiFetch<CollectionResponse<Group>>(`/groups${query}`),
  });
}

export function useGroup(groupId: string | undefined) {
  return useQuery<Group, ApiError>({
    queryKey: ["groups", "detail", groupId],
    queryFn: () => apiFetch<Group>(`/groups/${groupId}`),
    enabled: Boolean(groupId),
  });
}

export function useGroupMembers(groupId: string | undefined) {
  return useQuery<CollectionResponse<GroupMembership>, ApiError>({
    queryKey: ["groups", "members", groupId],
    queryFn: () => apiFetch<CollectionResponse<GroupMembership>>(`/groups/${groupId}/members`),
    enabled: Boolean(groupId),
  });
}

/** `GET /api/v1/persons/{person_id}/groups` (people-api.md §17, TH-0116):
 * the reverse direction of `useGroupMembers` — every GroupMembership
 * reachable through this Person's own ClubMembership row(s). Used by
 * Person Detail's "Группы" tab so `club_membership_id` never becomes a
 * user-facing concept there. */
export function usePersonGroupMemberships(personId: string | undefined) {
  return useQuery<CollectionResponse<GroupMembership>, ApiError>({
    queryKey: ["persons", "groups", personId],
    queryFn: () =>
      apiFetch<CollectionResponse<GroupMembership>>(`/persons/${personId}/groups?page_size=50`),
    enabled: Boolean(personId),
  });
}

/** `POST /api/v1/groups/{group_id}/members` (people-api.md §15) — the
 * SAME canonical endpoint from both directions this app offers it from:
 * Person Detail's "Группы" tab ("+ Добавить в группу") and Group
 * Detail's "Участники" tab ("+ Добавить участника"). Accepts `person_id`
 * per the existing API contract — the backend resolves the target
 * Person's active ClubMembership in the Group's Club server-side; this
 * client never resolves or sends `club_membership_id` itself.
 * `valid_from` is always "now" (no date picker in this MVP UI, matching
 * TH-0116 §12's own instruction). */
export function useAddGroupMember() {
  const queryClient = useQueryClient();
  return useMutation<
    GroupMembership,
    ApiError,
    { groupId: string; personId: string }
  >({
    mutationFn: ({ groupId, personId }) =>
      apiFetch<GroupMembership>(`/groups/${groupId}/members`, {
        method: "POST",
        body: JSON.stringify({ person_id: personId, valid_from: new Date().toISOString() }),
      }),
    onSuccess: (_membership, { groupId, personId }) => {
      void queryClient.invalidateQueries({ queryKey: ["groups", "members", groupId] });
      void queryClient.invalidateQueries({ queryKey: ["persons", "groups", personId] });
    },
  });
}

const GROUP_SCHEDULE_HORIZON_DAYS = 180;

export function useGroupSchedule(groupId: string | undefined) {
  const range = useMemo(() => {
    const from = new Date();
    const to = new Date(from);
    to.setUTCDate(to.getUTCDate() + GROUP_SCHEDULE_HORIZON_DAYS);
    return { from: from.toISOString(), to: to.toISOString() };
  }, []);

  const query = new URLSearchParams(range).toString();

  return useQuery<CollectionResponse<CalendarItem>, ApiError>({
    queryKey: ["groups", "schedule", groupId, range.from, range.to],
    queryFn: () => apiFetch<CollectionResponse<CalendarItem>>(`/groups/${groupId}/schedule?${query}`),
    enabled: Boolean(groupId),
  });
}

export type CreateGroupInput = {
  club_id: string;
  name: string;
  description?: string;
  valid_from: string;
};

export function useCreateGroup() {
  const queryClient = useQueryClient();
  return useMutation<Group, ApiError, CreateGroupInput>({
    mutationFn: (input) =>
      apiFetch<Group>("/groups", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["groups", "list"] });
    },
  });
}

export function useArchiveGroup() {
  const queryClient = useQueryClient();
  return useMutation<Group, ApiError, string>({
    mutationFn: (groupId) => apiFetch<Group>(`/groups/${groupId}/archive`, { method: "POST" }),
    onSuccess: (group) => {
      void queryClient.invalidateQueries({ queryKey: ["groups", "list"] });
      queryClient.setQueryData(["groups", "detail", group.id], group);
    },
  });
}
