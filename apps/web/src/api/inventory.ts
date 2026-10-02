import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * Inventory («Склад») API — docs/05-api/endpoint-inventory.md §19
 * (Foundation, Slice 2 instances, Slice 3 quantity stock, Slice 4
 * issue/return). Every endpoint is Administrator-only; the backend stays
 * the authorization source of truth (a non-Administrator gets `403`).
 * Mutations only call the documented operations and refetch: stock,
 * instance state and issue balances are never computed here.
 */

export type InventoryRecordStatus = "active" | "archived";
export type InventoryStatusFilter = InventoryRecordStatus | "all";
export type AccountingMode = "quantity" | "instance";
export type InstanceState = "available" | "issued" | "in_repair" | "written_off";
export type MovementType =
  | "receipt"
  | "transfer"
  | "issue"
  | "return"
  | "write_off"
  | "adjustment"
  | "repair_start"
  | "repair_end"
  | "writeoff_reversal";

export type InventoryCategory = {
  id: string;
  name: string;
  status: InventoryRecordStatus;
};

export type InventoryUnit = {
  id: string;
  name: string;
  is_system: boolean;
  status: InventoryRecordStatus;
};

export type InventoryStorageLocation = {
  id: string;
  parent_id: string | null;
  name: string;
  status: InventoryRecordStatus;
};

export type InventoryItem = {
  id: string;
  name: string;
  category_id: string;
  unit_id: string;
  accounting_mode: AccountingMode;
  /** Whole kopecks, RUB (docs/04-domain/inventory.md §12). */
  current_cost_minor: number | null;
  status: InventoryRecordStatus;
  archived_at: string | null;
};

export type InventoryInstance = {
  id: string;
  item_id: string;
  inventory_number: string;
  manufacturer_barcode: string | null;
  manufacturer_serial_number: string | null;
  description: string | null;
  state: InstanceState;
  storage_location_id: string | null;
};

export type InventoryStock = {
  item_id: string;
  storage_location_id: string;
  quantity: number;
};

export type InventoryMovement = {
  id: string;
  item_id: string;
  instance_id: string | null;
  movement_type: MovementType;
  from_location_id: string | null;
  to_location_id: string | null;
  quantity: number | null;
  unit_cost_minor: number | null;
  comment: string | null;
  reverses_movement_id: string | null;
  /** Slice 4: the issue line of an `issue`/`return` (and of a lost
   * instance's `write_off`); `null` otherwise. */
  issue_line_id?: string | null;
  created_at: string;
};

/** The backend's maximum `page_size` for inventory collections. */
const MAX_PAGE_SIZE = 200;
export const INVENTORY_PAGE_SIZE = 20;

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

/** Every page of a reference collection (categories, units, locations,
 * items) — used to resolve ids to names and to build the location tree. */
async function fetchAllPages<T>(path: string, params: Record<string, string> = {}): Promise<T[]> {
  const items: T[] = [];
  let page = 1;
  let pages = 1;
  do {
    const response = await apiFetch<CollectionResponse<T>>(
      `${path}?${queryString({ ...params, page, page_size: MAX_PAGE_SIZE })}`,
    );
    items.push(...response.items);
    pages = response.pagination.pages;
    page += 1;
  } while (page <= pages);
  return items;
}

export function useInventoryCategories() {
  return useQuery<InventoryCategory[], ApiError>({
    queryKey: ["inventory", "categories", "all"],
    queryFn: () => fetchAllPages<InventoryCategory>("/inventory/categories", { status: "all" }),
  });
}

export function useInventoryUnits() {
  return useQuery<InventoryUnit[], ApiError>({
    queryKey: ["inventory", "units", "all"],
    queryFn: () => fetchAllPages<InventoryUnit>("/inventory/units", { status: "all" }),
  });
}

/** Every storage location, active and archived — the tree and the
 * id → name lookup; status filtering happens on this one list. */
export function useInventoryStorageLocations() {
  return useQuery<InventoryStorageLocation[], ApiError>({
    queryKey: ["inventory", "storage-locations", "all"],
    queryFn: () => fetchAllPages<InventoryStorageLocation>("/inventory/storage-locations", { status: "all" }),
  });
}

/** All items regardless of status — a lookup for names and filters. */
export function useAllInventoryItems() {
  return useQuery<InventoryItem[], ApiError>({
    queryKey: ["inventory", "items", "all-pages"],
    queryFn: () => fetchAllPages<InventoryItem>("/inventory/items", { status: "all" }),
  });
}

export function useInventoryItems(params: { status: InventoryStatusFilter; page: number }) {
  return useQuery<CollectionResponse<InventoryItem>, ApiError>({
    queryKey: ["inventory", "items", "list", params.status, params.page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryItem>>(
        `/inventory/items?${queryString({ ...params, page_size: INVENTORY_PAGE_SIZE })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useInventoryItem(itemId: string | undefined) {
  return useQuery<InventoryItem, ApiError>({
    queryKey: ["inventory", "items", "detail", itemId],
    queryFn: () => apiFetch<InventoryItem>(`/inventory/items/${itemId}`),
    enabled: Boolean(itemId),
  });
}

export function useInventoryStock(params: { itemId: string; locationId: string; page: number }) {
  return useQuery<CollectionResponse<InventoryStock>, ApiError>({
    queryKey: ["inventory", "stock", params.itemId, params.locationId, params.page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryStock>>(
        `/inventory/stock?${queryString({
          item_id: params.itemId,
          storage_location_id: params.locationId,
          page: params.page,
          page_size: INVENTORY_PAGE_SIZE,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

/** Every non-zero stock row of one quantity item — all pages, so an
 * item held in more locations than one page is never cut off. */
export function useInventoryItemStock(itemId: string | undefined) {
  return useQuery<InventoryStock[], ApiError>({
    queryKey: ["inventory", "items", "stock", itemId],
    queryFn: () => fetchAllPages<InventoryStock>(`/inventory/items/${itemId}/stock`),
    enabled: Boolean(itemId),
  });
}

export function useInventoryItemMovements(itemId: string | undefined, page: number) {
  return useQuery<CollectionResponse<InventoryMovement>, ApiError>({
    queryKey: ["inventory", "items", "movements", itemId, page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryMovement>>(
        `/inventory/items/${itemId}/movements?${queryString({ page, page_size: INVENTORY_PAGE_SIZE })}`,
      ),
    enabled: Boolean(itemId),
    placeholderData: keepPreviousData,
  });
}

export function useInventoryInstances(params: {
  itemId: string;
  state: InstanceState | "";
  locationId: string;
  page: number;
}) {
  return useQuery<CollectionResponse<InventoryInstance>, ApiError>({
    queryKey: ["inventory", "instances", "list", params.itemId, params.state, params.locationId, params.page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryInstance>>(
        `/inventory/instances?${queryString({
          item_id: params.itemId,
          state: params.state,
          storage_location_id: params.locationId,
          page: params.page,
          page_size: INVENTORY_PAGE_SIZE,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useInventoryInstance(instanceId: string | undefined) {
  return useQuery<InventoryInstance, ApiError>({
    queryKey: ["inventory", "instances", "detail", instanceId],
    queryFn: () => apiFetch<InventoryInstance>(`/inventory/instances/${instanceId}`),
    enabled: Boolean(instanceId),
  });
}

/** Chronological history of one instance — returned as a plain list. */
export function useInventoryInstanceMovements(instanceId: string | undefined) {
  return useQuery<InventoryMovement[], ApiError>({
    queryKey: ["inventory", "instances", "movements", instanceId],
    queryFn: () => apiFetch<InventoryMovement[]>(`/inventory/instances/${instanceId}/movements`),
    enabled: Boolean(instanceId),
  });
}

// --- Slice 4: issue / return (docs/04-domain/inventory.md §14) ---------------

export type RecipientType = "member" | "instructor" | "group";
export type IssueStatus = "issued" | "cancelled";
export type IssueLineStatusFilter = "active" | "removed" | "all";

export type InventoryIssue = {
  id: string;
  recipient_type: RecipientType;
  /** Member → Person id, Instructor → User id, Group → Group id. */
  recipient_id: string;
  event_id: string | null;
  planned_return_date: string | null;
  comment: string | null;
  status: IssueStatus;
  /** Derived by the backend: something on the issue is still issued. */
  has_outstanding: boolean;
  cancelled_at: string | null;
  cancelled_by: string | null;
  created_by: string;
  created_at: string;
  updated_by: string | null;
  updated_at: string;
};

export type InventoryIssueLine = {
  id: string;
  issue_id: string;
  item_id: string;
  accounting_mode: AccountingMode;
  issued_quantity: number;
  returned_quantity: number;
  outstanding_quantity: number;
  outstanding_instance_ids: string[];
  created_by: string;
  created_at: string;
  removed_at: string | null;
  removed_by: string | null;
};

export type InventoryIssueDetail = InventoryIssue & {
  /** Active lines only; removed lines come from the lines endpoint. */
  lines: InventoryIssueLine[];
};

export type IssueLineInput =
  | { item_id: string; quantity: number }
  | { item_id: string; instance_ids: string[] };

export function useInventoryIssues(params: {
  status: IssueStatus | "";
  recipientType: RecipientType | "";
  page: number;
}) {
  return useQuery<CollectionResponse<InventoryIssue>, ApiError>({
    queryKey: ["inventory", "issues", "list", params.status, params.recipientType, params.page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryIssue>>(
        `/inventory/issues?${queryString({
          status: params.status,
          recipient_type: params.recipientType,
          page: params.page,
          page_size: INVENTORY_PAGE_SIZE,
        })}`,
      ),
    placeholderData: keepPreviousData,
  });
}

export function useInventoryIssue(issueId: string | undefined) {
  return useQuery<InventoryIssueDetail, ApiError>({
    queryKey: ["inventory", "issues", "detail", issueId],
    queryFn: () => apiFetch<InventoryIssueDetail>(`/inventory/issues/${issueId}`),
    enabled: Boolean(issueId),
  });
}

/** Lines of an issue by the backend's `status=active|removed|all`. */
export function useInventoryIssueLines(issueId: string | undefined, status: IssueLineStatusFilter) {
  return useQuery<InventoryIssueLine[], ApiError>({
    queryKey: ["inventory", "issues", "lines", issueId, status],
    queryFn: () => fetchAllPages<InventoryIssueLine>(`/inventory/issues/${issueId}/lines`, { status }),
    enabled: Boolean(issueId),
  });
}

export function useInventoryIssueMovements(issueId: string | undefined, page: number) {
  return useQuery<CollectionResponse<InventoryMovement>, ApiError>({
    queryKey: ["inventory", "issues", "movements", issueId, page],
    queryFn: () =>
      apiFetch<CollectionResponse<InventoryMovement>>(
        `/inventory/issues/${issueId}/movements?${queryString({ page, page_size: INVENTORY_PAGE_SIZE })}`,
      ),
    enabled: Boolean(issueId),
    placeholderData: keepPreviousData,
  });
}

/** Every instance of an item in one state — e.g. the `available`
 * instances offered for an issue line. */
export function useInventoryItemInstances(itemId: string | undefined, state: InstanceState) {
  return useQuery<InventoryInstance[], ApiError>({
    queryKey: ["inventory", "instances", "by-item", itemId, state],
    queryFn: () => fetchAllPages<InventoryInstance>("/inventory/instances", { item_id: itemId ?? "", state }),
    enabled: Boolean(itemId),
  });
}

// --- mutations -------------------------------------------------------------------

/** Any inventory write can change stock, instance states, item
 * archivability and issue balances, so every mutation refetches the
 * whole `["inventory"]` key space — the backend stays the source of the
 * new numbers. */
function useInventoryMutation<TResult, TInput>(mutationFn: (input: TInput) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation<TResult, ApiError, TInput>({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inventory"] }),
  });
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

// Reference data (Foundation).

export function useCreateInventoryCategory() {
  return useInventoryMutation((input: { name: string }) =>
    post<InventoryCategory>("/inventory/categories", input),
  );
}

export function useUpdateInventoryCategory() {
  return useInventoryMutation(({ id, name }: { id: string; name: string }) =>
    patch<InventoryCategory>(`/inventory/categories/${id}`, { name }),
  );
}

export function useArchiveInventoryCategory() {
  return useInventoryMutation((id: string) => post<InventoryCategory>(`/inventory/categories/${id}/archive`));
}

export function useCreateInventoryUnit() {
  return useInventoryMutation((input: { name: string }) => post<InventoryUnit>("/inventory/units", input));
}

export function useUpdateInventoryUnit() {
  return useInventoryMutation(({ id, name }: { id: string; name: string }) =>
    patch<InventoryUnit>(`/inventory/units/${id}`, { name }),
  );
}

export function useArchiveInventoryUnit() {
  return useInventoryMutation((id: string) => post<InventoryUnit>(`/inventory/units/${id}/archive`));
}

export function useCreateInventoryLocation() {
  return useInventoryMutation((input: { name: string; parent_id: string | null }) =>
    post<InventoryStorageLocation>("/inventory/storage-locations", input),
  );
}

export function useUpdateInventoryLocation() {
  return useInventoryMutation(
    ({ id, fields }: { id: string; fields: { name?: string; parent_id?: string | null } }) =>
      patch<InventoryStorageLocation>(`/inventory/storage-locations/${id}`, fields),
  );
}

export function useArchiveInventoryLocation() {
  return useInventoryMutation((id: string) =>
    post<InventoryStorageLocation>(`/inventory/storage-locations/${id}/archive`),
  );
}

export type InventoryItemFields = {
  name: string;
  category_id: string;
  unit_id: string;
  accounting_mode: AccountingMode;
  current_cost_minor: number | null;
};

export function useCreateInventoryItem() {
  return useInventoryMutation((input: InventoryItemFields) => post<InventoryItem>("/inventory/items", input));
}

/** PATCH with only the changed fields; immutability of the accounting
 * mode/unit after the first movement is decided by the backend. */
export function useUpdateInventoryItem() {
  return useInventoryMutation(({ id, fields }: { id: string; fields: Partial<InventoryItemFields> }) =>
    patch<InventoryItem>(`/inventory/items/${id}`, fields),
  );
}

export function useArchiveInventoryItem() {
  return useInventoryMutation((id: string) => post<InventoryItem>(`/inventory/items/${id}/archive`));
}

// Quantity operations (Slice 3).

export function useReceiveInventoryQuantity() {
  return useInventoryMutation(
    ({
      itemId,
      ...body
    }: {
      itemId: string;
      storage_location_id: string;
      quantity: number;
      unit_cost_minor: number | null;
      comment: string | null;
    }) => post<InventoryMovement>(`/inventory/items/${itemId}/receipts`, body),
  );
}

export function useTransferInventoryQuantity() {
  return useInventoryMutation(
    ({
      itemId,
      ...body
    }: {
      itemId: string;
      from_location_id: string;
      to_location_id: string;
      quantity: number;
      comment: string | null;
    }) => post<InventoryMovement>(`/inventory/items/${itemId}/transfers`, body),
  );
}

export function useWriteOffInventoryQuantity() {
  return useInventoryMutation(
    ({ itemId, ...body }: { itemId: string; storage_location_id: string; quantity: number; comment: string }) =>
      post<InventoryMovement>(`/inventory/items/${itemId}/write-offs`, body),
  );
}

/** Reversal of a write-off — the quantity endpoint for a quantity item,
 * the instance endpoint otherwise. `storage_location_id` is sent only
 * when the backend asked for it (the original location is archived). */
export function useReverseInventoryWriteOff() {
  return useInventoryMutation(
    ({
      movement,
      accountingMode,
      storageLocationId,
    }: {
      movement: InventoryMovement;
      accountingMode: AccountingMode;
      storageLocationId?: string;
    }): Promise<InventoryMovement | InventoryInstance> => {
      const body = storageLocationId ? { storage_location_id: storageLocationId } : {};
      return accountingMode === "quantity"
        ? post<InventoryMovement>(`/inventory/items/${movement.item_id}/write-offs/${movement.id}/reverse`, body)
        : post<InventoryInstance>(`/inventory/movements/${movement.id}/reverse`, body);
    },
  );
}

// Instance operations (Slice 2).

export function useCreateInventoryInstance() {
  return useInventoryMutation(
    (input: {
      item_id: string;
      storage_location_id: string;
      unit_cost_minor: number | null;
      manufacturer_barcode: string | null;
      manufacturer_serial_number: string | null;
      description: string | null;
    }) => post<InventoryInstance>("/inventory/instances", input),
  );
}

export function useUpdateInventoryInstance() {
  return useInventoryMutation(
    ({
      id,
      fields,
    }: {
      id: string;
      fields: {
        manufacturer_barcode: string | null;
        manufacturer_serial_number: string | null;
        description: string | null;
      };
    }) => patch<InventoryInstance>(`/inventory/instances/${id}`, fields),
  );
}

export function useTransferInventoryInstance() {
  return useInventoryMutation(
    ({ id, ...body }: { id: string; to_location_id: string; comment: string | null }) =>
      post<InventoryInstance>(`/inventory/instances/${id}/transfer`, body),
  );
}

export function useInstanceRepair() {
  return useInventoryMutation(({ id, action }: { id: string; action: "repair-start" | "repair-end" }) =>
    post<InventoryInstance>(`/inventory/instances/${id}/${action}`),
  );
}

export function useWriteOffInventoryInstance() {
  return useInventoryMutation(({ id, comment }: { id: string; comment: string }) =>
    post<InventoryInstance>(`/inventory/instances/${id}/write-off`, { comment }),
  );
}

// Issue / return (Slice 4).

export function useCreateInventoryIssue() {
  return useInventoryMutation(
    (input: {
      recipient_type: RecipientType;
      recipient_id: string;
      event_id: string | null;
      planned_return_date: string | null;
      comment: string | null;
      lines: IssueLineInput[];
    }) => post<InventoryIssueDetail>("/inventory/issues", input),
  );
}

export type InventoryIssueHeaderFields = {
  recipient_type?: RecipientType;
  recipient_id?: string;
  event_id?: string | null;
  planned_return_date?: string | null;
  comment?: string | null;
};

export function useUpdateInventoryIssue() {
  return useInventoryMutation(({ id, fields }: { id: string; fields: InventoryIssueHeaderFields }) =>
    patch<InventoryIssueDetail>(`/inventory/issues/${id}`, fields),
  );
}

export function useAddInventoryIssueLines() {
  return useInventoryMutation(({ id, lines }: { id: string; lines: IssueLineInput[] }) =>
    post<InventoryIssueDetail>(`/inventory/issues/${id}/lines`, { lines }),
  );
}

export function useRemoveInventoryIssueLine() {
  return useInventoryMutation(({ issueId, lineId }: { issueId: string; lineId: string }) =>
    apiFetch<InventoryIssueDetail>(`/inventory/issues/${issueId}/lines/${lineId}`, { method: "DELETE" }),
  );
}

export function useReturnInventoryIssue() {
  return useInventoryMutation(
    ({
      id,
      ...body
    }: {
      id: string;
      storage_location_id: string;
      quantities: Array<{ line_id: string; quantity: number }>;
      instance_ids: string[];
      comment: string | null;
    }) => post<InventoryIssueDetail>(`/inventory/issues/${id}/returns`, body),
  );
}

export function useCancelInventoryIssue() {
  return useInventoryMutation(({ id, storage_location_id }: { id: string; storage_location_id: string }) =>
    post<InventoryIssueDetail>(`/inventory/issues/${id}/cancel`, { storage_location_id }),
  );
}

export function useReportLostInventoryInstance() {
  return useInventoryMutation(
    ({
      id,
      ...body
    }: {
      id: string;
      instance_id: string;
      storage_location_id: string;
      reason: string;
      comment: string | null;
    }) => post<InventoryIssueDetail>(`/inventory/issues/${id}/lost`, body),
  );
}
