/**
 * Presentation of Inventory («Склад») values — Russian labels from the
 * canonical domain (docs/04-domain/inventory.md §5, §7, §13) and the
 * semantic mapping of states onto the closed Status icon catalog
 * (docs/06-ui/assets/ASSET-STATUS.md; see also statusMapping.ts).
 * Formatting only: no inventory rule is re-implemented here.
 */

import type { StatusIconId } from "../assets/icons";
import type { FilterOption } from "../components/ui/FilterSelect";
import type {
  AccountingMode,
  InstanceState,
  InventoryRecordStatus,
  InventoryStorageLocation,
  MovementType,
} from "../api/inventory";

export function accountingModeLabel(mode: AccountingMode): string {
  switch (mode) {
    case "quantity":
      return "Количественный учёт";
    case "instance":
      return "Поэкземплярный учёт";
  }
}

export function recordStatusIcon(status: InventoryRecordStatus): StatusIconId {
  switch (status) {
    case "active":
      return "status.ongoing";
    case "archived":
      return "status.archived";
  }
}

export function recordStatusLabel(status: InventoryRecordStatus): string {
  switch (status) {
    case "active":
      return "Активна";
    case "archived":
      return "В архиве";
  }
}

export function instanceStateIcon(state: InstanceState): StatusIconId {
  switch (state) {
    case "available":
      return "status.success";
    case "issued":
      return "status.ongoing";
    case "in_repair":
      return "status.warning";
    case "written_off":
      return "status.ended";
  }
}

export function instanceStateLabel(state: InstanceState): string {
  switch (state) {
    case "available":
      return "В наличии";
    case "issued":
      return "Выдан";
    case "in_repair":
      return "В ремонте";
    case "written_off":
      return "Списан";
  }
}

/** Instance state filter; without a state the API returns every
 * instance except `written_off` (docs/04-domain/inventory.md §7). */
export const INSTANCE_STATE_OPTIONS: FilterOption[] = [
  { value: "", label: "Все, кроме списанных" },
  { value: "available", label: instanceStateLabel("available") },
  { value: "issued", label: instanceStateLabel("issued") },
  { value: "in_repair", label: instanceStateLabel("in_repair") },
  { value: "written_off", label: instanceStateLabel("written_off") },
];

export function movementTypeLabel(type: MovementType): string {
  switch (type) {
    case "receipt":
      return "Приём";
    case "transfer":
      return "Перемещение";
    case "issue":
      return "Выдача";
    case "return":
      return "Возврат";
    case "write_off":
      return "Списание";
    case "adjustment":
      return "Корректировка";
    case "repair_start":
      return "Начало ремонта";
    case "repair_end":
      return "Окончание ремонта";
    case "writeoff_reversal":
      return "Отмена списания";
  }
}

const RUB = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB" });

/** Whole kopecks (RUB, §12) → «1 234,50 ₽»; `null` → «не указана». */
export function formatCostMinor(minor: number | null): string {
  if (minor === null) return "не указана";
  return RUB.format(minor / 100);
}

export function formatMovementDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** «Склад › Стеллаж 1 › Полка 2» — the location's place in the tree. */
export function locationPath(
  locationId: string | null,
  locations: ReadonlyMap<string, InventoryStorageLocation>,
): string {
  if (!locationId) return "—";
  const names: string[] = [];
  const seen = new Set<string>();
  let current = locations.get(locationId);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    names.unshift(current.name);
    current = current.parent_id ? locations.get(current.parent_id) : undefined;
  }
  return names.length > 0 ? names.join(" › ") : "Неизвестное место";
}

export type LocationTreeNode = {
  location: InventoryStorageLocation;
  children: LocationTreeNode[];
};

/** Roots first, children under their parent, each level sorted by name. */
export function buildLocationTree(locations: readonly InventoryStorageLocation[]): LocationTreeNode[] {
  const nodes = new Map<string, LocationTreeNode>();
  for (const location of locations) nodes.set(location.id, { location, children: [] });
  const roots: LocationTreeNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.location.parent_id ? nodes.get(node.location.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const byName = (a: LocationTreeNode, b: LocationTreeNode) =>
    a.location.name.localeCompare(b.location.name, "ru");
  const sort = (list: LocationTreeNode[]) => {
    list.sort(byName);
    list.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}
