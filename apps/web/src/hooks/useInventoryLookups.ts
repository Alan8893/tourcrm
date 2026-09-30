import { useMemo } from "react";

import {
  useAllInventoryItems,
  useInventoryCategories,
  useInventoryStorageLocations,
  useInventoryUnits,
  type InventoryCategory,
  type InventoryItem,
  type InventoryStorageLocation,
  type InventoryUnit,
} from "../api/inventory";

export type InventoryLookups = {
  categories: ReadonlyMap<string, InventoryCategory>;
  units: ReadonlyMap<string, InventoryUnit>;
  locations: ReadonlyMap<string, InventoryStorageLocation>;
  items: ReadonlyMap<string, InventoryItem>;
};

function byId<T extends { id: string }>(records: readonly T[] | undefined): Map<string, T> {
  return new Map((records ?? []).map((record) => [record.id, record]));
}

/** Reference data (all statuses) that turns ids in inventory rows into
 * names: categories, units, storage locations and items. */
export function useInventoryLookups() {
  const categories = useInventoryCategories();
  const units = useInventoryUnits();
  const locations = useInventoryStorageLocations("all");
  const items = useAllInventoryItems();
  const queries = [categories, units, locations, items];

  const lookups = useMemo<InventoryLookups>(
    () => ({
      categories: byId(categories.data),
      units: byId(units.data),
      locations: byId(locations.data),
      items: byId(items.data),
    }),
    [categories.data, units.data, locations.data, items.data],
  );

  return {
    lookups,
    isLoading: queries.some((query) => query.isLoading),
    error: queries.find((query) => query.isError)?.error ?? null,
    refetch: () => queries.forEach((query) => void query.refetch()),
  };
}

export function unitName(item: InventoryItem | undefined, lookups: InventoryLookups): string {
  return (item && lookups.units.get(item.unit_id)?.name) ?? "";
}
