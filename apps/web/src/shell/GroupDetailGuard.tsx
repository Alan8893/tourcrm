import { Outlet } from "react-router-dom";

import { useCurrentUser } from "../api/auth";
import { isNavigationItemVisible } from "./navigation";
import { SectionForbidden } from "./SectionGuard";

/** Group Detail route guard (ADR-0046, Issue #301). The «Группы» section
 * stays exactly as `SectionGuard` defines it — Guardian has no Groups
 * navigation item and `/groups` keeps rendering the 403 state for them —
 * but Guardian reaches the existing Group Detail contextually, from
 * «Мои дети» on Home (`Guardian → Child → Group`). So this route opens
 * for every role the «Группы» section is visible to, plus `guardian`.
 * UX only: `GET /groups/{id}` authorizes the Group itself (`group.read`
 * `children` for Guardian) and hides any other Group as not found. */
export function GroupDetailGuard() {
  const { data } = useCurrentUser();
  const roleAssignments = data?.role_assignments ?? [];
  const allowed =
    isNavigationItemVisible(roleAssignments, "groups") ||
    roleAssignments.some(({ role_code }) => role_code === "guardian");

  return allowed ? <Outlet /> : <SectionForbidden />;
}
