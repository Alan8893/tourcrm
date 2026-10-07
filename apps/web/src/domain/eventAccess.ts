/**
 * Role-aware Event UI (Issue #299; docs/04-ux/information-architecture.md
 * «Карточка Event: вкладка «Участники»»; role-permission-scope-matrix.md §7):
 *
 * | Role          | Management controls | Event detail tabs        |
 * |---------------|---------------------|--------------------------|
 * | Administrator | yes                 | Обзор / Участники        |
 * | Instructor    | yes                 | Обзор / Участники        |
 * | Member        | no                  | — (overview only)        |
 * | Guardian      | no                  | — (overview only)        |
 *
 * Member and Guardian hold `event.read` only (`self` / `children`) and no
 * Event create/update/cancel/manage permission, so they get neither the
 * management actions nor the instructor-management filters; their
 * self-registration stays in the overview. A user holding several roles
 * gets the union. «Участники» exists only for an ordinary Event — a
 * recurring occurrence id is not an `event_id` of the participants API.
 *
 * UX only — never a security boundary: the backend authorizes every Event
 * operation and every roster row on its own (`event.read` scope).
 */
export type EventDetailTabId = "overview" | "participants";

function isEventStaff(roleCodes: readonly string[]): boolean {
  return roleCodes.includes("admin") || roleCodes.includes("instructor");
}

/** Event management actions (create, edit, lifecycle) and the
 * instructor-management calendar filters. */
export function canManageEvents(roleCodes: readonly string[]): boolean {
  return isEventStaff(roleCodes);
}

export function eventDetailTabIds(
  roleCodes: readonly string[],
  kind: "event" | "occurrence",
): EventDetailTabId[] {
  return kind === "event" && isEventStaff(roleCodes) ? ["overview", "participants"] : ["overview"];
}
