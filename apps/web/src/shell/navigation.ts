import type { RoleAssignmentSummary } from "../api/auth";
import type { PersonRoleCode } from "../api/people";
import type { NavigationIconId } from "../assets/icons";

export type NavigationItemId =
  | "home"
  | "people"
  | "groups"
  | "events"
  | "achievements"
  | "reports"
  | "settings";

export type NavigationItem = {
  id: NavigationItemId;
  label: string;
  path: string;
  icon: NavigationIconId;
};

/**
 * The approved Navigation Architecture (CLOSED — docs/06-ui/assets/
 * packages/navigation/README.md, ASSET-STATUS.md): exactly these seven
 * items, in this order, with this wording. Do not add, remove, reorder
 * or rename entries here without an explicit Product Owner decision that
 * reopens Navigation Architecture v1.0.
 */
export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { id: "home", label: "Главная", path: "/", icon: "nav.home" },
  { id: "people", label: "Люди", path: "/people", icon: "nav.people" },
  { id: "groups", label: "Группы", path: "/groups", icon: "nav.groups" },
  { id: "events", label: "События", path: "/events", icon: "nav.events" },
  {
    id: "achievements",
    label: "Достижения",
    path: "/achievements",
    icon: "nav.achievements",
  },
  { id: "reports", label: "Отчёты", path: "/reports", icon: "nav.reports" },
  { id: "settings", label: "Настройки", path: "/settings", icon: "nav.settings" },
] as const;

/**
 * Role-aware visibility matrix (TH-0120 / Issue #181,
 * docs/04-ux/information-architecture.md §3.1), keyed by the canonical
 * `role_code` values returned in `/auth/me` `role_assignments`. This is UI
 * visibility only — backend authorization remains authoritative for every
 * route, whether or not its navigation item is shown.
 */
export const NAVIGATION_VISIBILITY: Readonly<Record<PersonRoleCode, readonly NavigationItemId[]>> = {
  admin: ["home", "people", "groups", "events", "achievements", "reports", "settings"],
  instructor: ["home", "people", "groups", "events", "achievements", "settings"],
  member: ["home", "groups", "events", "achievements", "settings"],
  guardian: ["home", "events", "achievements", "settings"],
};

function isMatrixRole(roleCode: string): roleCode is PersonRoleCode {
  return Object.prototype.hasOwnProperty.call(NAVIGATION_VISIBILITY, roleCode);
}

/**
 * Multi-role UNION (PO decision 2026-09-23): an item is visible when at
 * least one of the user's current role assignments grants it. The result
 * keeps the canonical catalog order. A role code absent from the matrix
 * grants nothing; there is no active-role selection.
 */
export function visibleNavigationItems(
  roleAssignments: readonly Pick<RoleAssignmentSummary, "role_code">[],
): NavigationItem[] {
  const allowed = new Set<NavigationItemId>();
  for (const { role_code } of roleAssignments) {
    if (isMatrixRole(role_code)) {
      NAVIGATION_VISIBILITY[role_code].forEach((id) => allowed.add(id));
    }
  }
  return NAVIGATION_ITEMS.filter((item) => allowed.has(item.id));
}
