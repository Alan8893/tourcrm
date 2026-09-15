/**
 * Maps real backend domain status values onto the approved, closed Status
 * icon catalog (docs/06-ui/assets/ASSET-STATUS.md).
 *
 * ASSET-STATUS.md is explicit that "Domain status mapping must be
 * semantic and must not assume `domain status == asset name`" and gives
 * `cancelled` as the worked example of a status that must NOT be mapped
 * to `error` — a cancellation is a business outcome, not a system
 * fault. It maps to `ended` instead (the approved extinguished-campfire
 * metaphor: "this stopped happening"), matching the semantic intent
 * without borrowing the fault/error connotation.
 */

import type { StatusIconId } from "../assets/icons";

export type GroupStatus = "active" | "archived";

export function groupStatusIcon(status: GroupStatus): StatusIconId {
  switch (status) {
    case "active":
      return "status.ongoing";
    case "archived":
      return "status.archived";
  }
}

export function groupStatusLabel(status: GroupStatus): string {
  switch (status) {
    case "active":
      return "Активна";
    case "archived":
      return "Архивная";
  }
}

export type EventStatus =
  | "draft"
  | "published"
  | "in_progress"
  | "completed"
  | "cancelled"
  | "archived";

export function eventStatusIcon(status: EventStatus): StatusIconId {
  switch (status) {
    case "draft":
    case "published":
      return "status.planned";
    case "in_progress":
      return "status.ongoing";
    case "completed":
      return "status.completed";
    case "cancelled":
      // Explicitly not `status.error` — see module docstring.
      return "status.ended";
    case "archived":
      return "status.archived";
  }
}

export function eventStatusLabel(status: EventStatus): string {
  switch (status) {
    case "draft":
      return "Черновик";
    case "published":
      return "Запланировано";
    case "in_progress":
      return "Идёт сейчас";
    case "completed":
      return "Завершено";
    case "cancelled":
      return "Отменено";
    case "archived":
      return "В архиве";
  }
}
