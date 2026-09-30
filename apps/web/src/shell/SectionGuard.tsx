import { Link, Outlet } from "react-router-dom";

import { useCurrentUser } from "../api/auth";
import { Button } from "../components/ui/Button";
import { ErrorState } from "../components/ui/ErrorState";
import { isNavigationItemVisible, type NavigationItemId } from "./navigation";

export type SectionGuardProps = {
  section: NavigationItemId;
};

/** Issue #212: a direct visit (typed URL, bookmark, stale link) to a
 * section the signed-in user's roles do not show in navigation renders the
 * standard 403 state instead of that section's page — so no management UI
 * (create/edit actions, lists) of a hidden section is exposed. It applies
 * the same canonical matrix and UNION as the navigation itself
 * (`NAVIGATION_VISIBILITY`); it is UX only and introduces no permission of
 * its own — backend authorization stays authoritative for every request.
 * Renders only inside AppShell's authenticated shell, so `/auth/me` is
 * already resolved here. */
export function SectionGuard({ section }: SectionGuardProps) {
  const { data } = useCurrentUser();

  if (!isNavigationItemVisible(data?.role_assignments ?? [], section)) {
    return <SectionForbidden />;
  }

  return <Outlet />;
}

/** The standard 403 state of a guarded route — shared by `SectionGuard`
 * and `AdministratorGuard` so both render the identical forbidden page. */
export function SectionForbidden() {
  return (
    <ErrorState
      illustration="403"
      title="Раздел недоступен"
      description="У вас нет доступа к этому разделу."
      action={
        <Link to="/">
          <Button variant="primary">На главную</Button>
        </Link>
      }
    />
  );
}
