import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";
import type { GroupStatus } from "../domain/statusMapping";

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

export function useGroupSchedule(groupId: string | undefined) {
  return useQuery<CollectionResponse<CalendarItem>, ApiError>({
    queryKey: ["groups", "schedule", groupId],
    queryFn: () => apiFetch<CollectionResponse<CalendarItem>>(`/groups/${groupId}/schedule`),
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
