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

/** business-rules.md §4: ClubMembership's status vocabulary. */
export type MembershipStatus = "pending" | "active" | "suspended" | "inactive" | "archived";

export function membershipStatusIcon(status: MembershipStatus): StatusIconId {
  switch (status) {
    case "pending":
      return "status.planned";
    case "active":
      return "status.ongoing";
    case "suspended":
      // Not `error`: a suspension is a reviewable, non-terminal business
      // state, not a system fault — `warning` carries that "needs
      // attention" meaning without the fault connotation.
      return "status.warning";
    case "inactive":
      return "status.ended";
    case "archived":
      return "status.archived";
  }
}

export function membershipStatusLabel(status: MembershipStatus): string {
  switch (status) {
    case "pending":
      return "Ожидает";
    case "active":
      return "Активно";
    case "suspended":
      return "Приостановлено";
    case "inactive":
      return "Неактивно";
    case "archived":
      return "В архиве";
  }
}

/** people-api.md §18: GuardianRelationship's read-time effective status —
 * already derived server-side (never `pending`; see
 * app.people.guardian_lifecycle.effective_status). */
export type GuardianRelationshipStatus = "active" | "inactive" | "revoked";

export function guardianRelationshipStatusIcon(status: GuardianRelationshipStatus): StatusIconId {
  switch (status) {
    case "active":
      return "status.ongoing";
    case "inactive":
      return "status.ended";
    case "revoked":
      // Not `error`: revocation is a deliberate business outcome, not a
      // system fault — `archived` matches its terminal, non-fault nature
      // (mirrors `cancelled -> ended` above, not `-> error`).
      return "status.archived";
  }
}

export function guardianRelationshipStatusLabel(status: GuardianRelationshipStatus): string {
  switch (status) {
    case "active":
      return "Активна";
    case "inactive":
      return "Неактивна";
    case "revoked":
      return "Отозвана";
  }
}
