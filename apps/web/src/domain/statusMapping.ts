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

/** `User.status` (TH-0113 / ADR-0038) — the same canonical account-
 * lifecycle vocabulary auth-and-authorization.md §4 documents; never a
 * new invented value. */
export type AccountStatus = "pending" | "active" | "locked" | "suspended" | "disabled" | "archived";

export function accountStatusIcon(status: AccountStatus): StatusIconId {
  switch (status) {
    case "pending":
      return "status.planned";
    case "active":
      return "status.success";
    case "locked":
    case "suspended":
      return "status.warning";
    case "disabled":
      return "status.error";
    case "archived":
      return "status.archived";
  }
}

export function accountStatusLabel(status: AccountStatus): string {
  switch (status) {
    case "pending":
      return "Ожидает активации";
    case "active":
      return "Активна";
    case "locked":
      return "Заблокирована";
    case "suspended":
      return "Приостановлена";
    case "disabled":
      return "Отключена";
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

/** app.events.vocabulary.CANONICAL_EVENT_TYPES (events-and-schedule.md §3) —
 * the closed, backend-validated event_type vocabulary. Calendar create/edit
 * forms and filters must only offer these values. */
export type EventType =
  | "lesson"
  | "training"
  | "trip"
  | "competition"
  | "tour_slet"
  | "excursion"
  | "meeting"
  | "other";

export const CANONICAL_EVENT_TYPES: readonly EventType[] = [
  "lesson",
  "training",
  "trip",
  "competition",
  "tour_slet",
  "excursion",
  "meeting",
  "other",
];

export function eventTypeLabel(eventType: string): string {
  switch (eventType as EventType) {
    case "lesson":
      return "Занятие";
    case "training":
      return "Тренировка";
    case "trip":
      return "Поход";
    case "competition":
      return "Соревнование";
    case "tour_slet":
      return "Слёт";
    case "excursion":
      return "Экскурсия";
    case "meeting":
      return "Собрание";
    case "other":
      return "Другое";
    default:
      return eventType;
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

/** ADR-0040 §4 (`app.db.documents.CANONICAL_DOCUMENT_STATUSES`): the
 * persisted `Document.status` value returned as-is by every participant
 * Document endpoint (people-api.md §32). This is a *stored* status, not a
 * read-time recomputation: `expires_at` elapsing does not rewrite it to
 * `expired` server-side, so the frontend must render exactly this value
 * and must never derive its own "is it expired today" verdict from
 * `expires_at` — that would be exactly the client-side validity
 * calculation the Issue #175 business rule forbids. */
export type DocumentStatus = "active" | "expired" | "revoked";

export function documentStatusIcon(status: DocumentStatus): StatusIconId {
  switch (status) {
    case "active":
      return "status.success";
    case "expired":
      return "status.warning";
    case "revoked":
      // Not `error`: revocation is a deliberate business outcome, not a
      // system fault — mirrors GuardianRelationshipStatus's identical
      // `revoked -> archived` precedent above.
      return "status.archived";
  }
}

export function documentStatusLabel(status: DocumentStatus): string {
  switch (status) {
    case "active":
      return "Действителен";
    case "expired":
      return "Истёк";
    case "revoked":
      return "Отозван";
  }
}

/** `medical_certificate` gets the dedicated Issue #175 label; every other
 * `document_type` remains an open string vocabulary (ADR-0040 §1,
 * events-api.md §31.1) and is rendered as-is. */
export function documentTypeLabel(documentType: string): string {
  return documentType === "medical_certificate" ? "Медицинская справка" : documentType;
}

/** `EventDocumentRequirementCheckOut.result` (events-api.md §31.3): a
 * derived, backend-computed three-value result — never calculated
 * client-side. `revoked` is explicitly never a fourth value here (a
 * revoked current Document maps to `expired` server-side, ADR-0040 §5). */
export type DocumentRequirementResult = "valid" | "missing" | "expired";

export function documentRequirementResultIcon(result: DocumentRequirementResult): StatusIconId {
  switch (result) {
    case "valid":
      return "status.success";
    case "expired":
      return "status.warning";
    case "missing":
      // More severe than `expired` (nothing on file at all, vs. a
      // superseded/elapsed document) — not `error` (a system fault),
      // since this is an ordinary, expected operational state, but the
      // most attention-demanding of the three.
      return "status.error";
  }
}

export function documentRequirementResultLabel(result: DocumentRequirementResult): string {
  switch (result) {
    case "valid":
      return "Действителен";
    case "expired":
      return "Истёк";
    case "missing":
      return "Отсутствует";
  }
}
