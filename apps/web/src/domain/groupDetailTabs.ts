/**
 * Role-aware Group Detail tabs (Issue #285, PO decision 2026-10-06):
 *
 * | Role          | Tabs                          |
 * |---------------|-------------------------------|
 * | Administrator | Обзор / Участники / Расписание |
 * | Instructor    | Участники / Расписание         |
 * | Member        | Расписание                     |
 *
 * A user holding several roles gets the union. Every other role sees only
 * the schedule tab, whose content the backend authorizes on its own
 * (ODR-0002). UX only — never a security boundary: each tab's data is
 * still authorized server-side.
 */
export type GroupDetailTabId = "overview" | "members" | "schedule";

export function groupDetailTabIds(roleCodes: readonly string[]): GroupDetailTabId[] {
  const isAdmin = roleCodes.includes("admin");
  const isInstructor = roleCodes.includes("instructor");
  const tabs: GroupDetailTabId[] = [];
  if (isAdmin) tabs.push("overview");
  if (isAdmin || isInstructor) tabs.push("members");
  tabs.push("schedule");
  return tabs;
}
