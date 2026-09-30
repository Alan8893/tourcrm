import { Outlet } from "react-router-dom";

import { useCurrentUser } from "../api/auth";
import { hasAdministratorRole } from "./navigation";
import { SectionForbidden } from "./SectionGuard";

/** TH-0118.5 (docs/04-ux/import-export-ui.md §8): Participant Import lives
 * inside «Люди», a section Instructor also sees — so the section-level
 * `SectionGuard` alone would let an Instructor open `/people/import` by
 * direct URL. This nested guard renders the same standard 403 state for
 * any user without the canonical `admin` role assignment. Same UX-only
 * role check as the existing `isAdmin` controls (PeoplePage,
 * GroupDetailPage, PersonDetailPage); it introduces no permission of its
 * own — backend authorization (`membership.import` / Administrator-only
 * export) stays authoritative for every request. */
export function AdministratorGuard() {
  const { data } = useCurrentUser();

  if (!hasAdministratorRole(data?.role_assignments ?? [])) {
    return <SectionForbidden />;
  }

  return <Outlet />;
}
