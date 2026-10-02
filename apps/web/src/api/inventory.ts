import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * Read side of the Inventory («Склад») API — docs/05-api/endpoint-inventory.md
 * §19 (Foundation, Slice 2 instances, Slice 3 quantity stock). Every
 * endpoint is Administrator-only; the backend stays the authorization
 * source of truth (a non-Administrator gets `403`).
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
