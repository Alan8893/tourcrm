import type { InventoryIssue, IssueLineInput } from "../api/inventory";
import type { StatusIconId } from "../assets/icons";
import { locationPath, parseQuantity } from "../domain/inventoryFormat";
import type { InventoryLookups } from "../hooks/useInventoryLookups";

/** Non-component helpers shared by the Inventory dialogs and pages:
 * select options and the draft → request mapping of issue lines.
 * Presentation and input shaping only — no inventory rule lives here. */

export type Option = { value: string; label: string };

export function byLabel(a: Option, b: Option) {
  return a.label.localeCompare(b.label, "ru");
}

/** Active storage locations by full path — the places an operation can
 * use (archived ones are read-only, §17). */
export function activeLocationOptions(lookups: InventoryLookups, exclude?: string): Option[] {
  return [...lookups.locations.values()]
    .filter((location) => location.status === "active" && location.id !== exclude)
    .map((location) => ({ value: location.id, label: locationPath(location.id, lookups.locations) }))
    .sort(byLabel);
}

export function issueStatusIcon(issue: Pick<InventoryIssue, "status" | "has_outstanding">): StatusIconId {
  if (issue.status === "cancelled") return "status.ended";
  return issue.has_outstanding ? "status.ongoing" : "status.completed";
}

export type DraftLine = { key: number; itemId: string; quantity: string; instanceIds: string[] };

let nextLineKey = 1;
export function newDraftLine(): DraftLine {
  nextLineKey += 1;
  return { key: nextLineKey, itemId: "", quantity: "", instanceIds: [] };
}

/** The request lines of a draft, or `null` while one is incomplete. */
export function draftLinesToInput(lines: DraftLine[], lookups: InventoryLookups): IssueLineInput[] | null {
  const result: IssueLineInput[] = [];
  for (const line of lines) {
    const item = lookups.items.get(line.itemId);
    if (!item) return null;
    if (item.accounting_mode === "quantity") {
      const quantity = parseQuantity(line.quantity);
      if (!quantity) return null;
      result.push({ item_id: item.id, quantity });
    } else {
      if (line.instanceIds.length === 0) return null;
      result.push({ item_id: item.id, instance_ids: line.instanceIds });
    }
  }
  return result.length > 0 ? result : null;
}

