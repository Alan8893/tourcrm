import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * User directory API client (TH-0107). `GET /api/v1/users` is a safe,
 * operational directory — not a full admin User Management API — used to
 * pick a person by name (e.g. Calendar's instructor filter). Response
 * shape matches `UserDirectoryOut` (app/api/v1/users_schemas.py) and
 * docs/05-api/users-api.md exactly: only these 5 fields are ever
 * returned, never authentication/session/role data.
 */
export type UserDirectoryEntry = {
  id: string;
  person_id: string;
  first_name: string;
  last_name: string;
  middle_name: string | null;
};

export function userFullName(user: UserDirectoryEntry): string {
  return [user.last_name, user.first_name, user.middle_name].filter(Boolean).join(" ");
}

const USER_DIRECTORY_PAGE_SIZE = 20;

/** `GET /api/v1/users?search&club_id&role&status&page&page_size`
 * (docs/05-api/users-api.md). All filtering/authorization happens
 * server-side (person.read + scope + the club/role result filters) —
 * this hook applies no additional client-side visibility logic. */
export function useUsers(params: {
  search?: string;
  club_id?: string;
  role?: string;
  status?: string;
  page?: number;
  /** Defaults to true. Pass `false` while a picker using this hook is
   * closed, so the directory isn't polled in the background. */
  enabled?: boolean;
}) {
  const page = params.page ?? 1;
  const trimmedSearch = params.search?.trim() ?? "";
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(USER_DIRECTORY_PAGE_SIZE),
  });
  if (trimmedSearch) query.set("search", trimmedSearch);
  if (params.club_id) query.set("club_id", params.club_id);
  if (params.role) query.set("role", params.role);
  if (params.status) query.set("status", params.status);

  return useQuery<CollectionResponse<UserDirectoryEntry>, ApiError>({
    queryKey: [
      "users",
      "list",
      page,
      trimmedSearch,
      params.club_id ?? "",
      params.role ?? "",
      params.status ?? "",
    ],
    queryFn: () => apiFetch<CollectionResponse<UserDirectoryEntry>>(`/users?${query.toString()}`),
    placeholderData: keepPreviousData,
    enabled: params.enabled ?? true,
  });
}

const USER_DIRECTORY_MAX_PAGE_SIZE = 100;

/** Every directory entry holding `role` — e.g. all instructors, so an
 * Instructor recipient (a User id) can be shown by name. The directory
 * has no by-id endpoint; this reads the same `GET /api/v1/users`. */
export function useAllUsersWithRole(role: string, enabled = true) {
  return useQuery<UserDirectoryEntry[], ApiError>({
    queryKey: ["users", "all-with-role", role],
    queryFn: async () => {
      const entries: UserDirectoryEntry[] = [];
      let page = 1;
      let pages = 1;
      do {
        const query = new URLSearchParams({
          role,
          page: String(page),
          page_size: String(USER_DIRECTORY_MAX_PAGE_SIZE),
        });
        const response = await apiFetch<CollectionResponse<UserDirectoryEntry>>(`/users?${query.toString()}`);
        entries.push(...response.items);
        pages = response.pagination.pages;
        page += 1;
      } while (page <= pages);
      return entries;
    },
    enabled,
  });
}
