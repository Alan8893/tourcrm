import type { NavigationIconId } from "../assets/icons";

export type NavigationItem = {
  id: string;
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
