import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useCurrentUser } from "../api/auth";
import { ApiError } from "../api/client";
import { Button } from "../components/ui/Button";
import { ErrorState } from "../components/ui/ErrorState";
import { BrandLogo } from "../components/ui/Icon";
import { Loading } from "../components/ui/Loading";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { MobileNavDrawer } from "./MobileNavDrawer";
import styles from "./AppShell.module.css";

/** The shared App Shell (spec §3): brand/header zone, persistent
 * navigation (Sidebar on desktop/tablet, a drawer on mobile) and a main
 * content area rendered once here and reused by every page via
 * `<Outlet />` — no page re-implements its own header/nav.
 *
 * TH-0089 / spec §3.1: the shell is an authenticated surface only. It is
 * gated on the existing `GET /auth/me` contract — nothing of the shell
 * (and therefore no protected page) renders until the backend confirms a
 * session, and there is no Guest pseudo-user: a 401 hands the visitor to
 * the existing `/login` entry (with the current route as its validated
 * `next` return target); any other failure stays here as a system error
 * rather than being mistaken for a logout. */
export function AppShell() {
  const me = useCurrentUser();
  const location = useLocation();
  const queryClient = useQueryClient();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  // A 401 on a refetch keeps the previously cached identity in `data`;
  // drop it (as `useLogout` does) so `/login` re-derives "signed out" from
  // the backend instead of trusting the stale identity and bouncing back.
  useEffect(() => {
    if (unauthenticated) queryClient.removeQueries({ queryKey: ["auth", "me"] });
  }, [unauthenticated, queryClient]);

  if (me.isPending) {
    return (
      <AuthBoundaryState>
        <Loading label="Проверка входа…" />
      </AuthBoundaryState>
    );
  }

  if (me.isError) {
    if (unauthenticated) {
      const returnTarget = `${location.pathname}${location.search}${location.hash}`;
      const loginPath =
        returnTarget === "/" ? "/login" : `/login?next=${encodeURIComponent(returnTarget)}`;
      return <Navigate to={loginPath} replace />;
    }
    return (
      <AuthBoundaryState>
        <ErrorState
          illustration="error"
          title="Не удалось проверить вход"
          description="Сервер временно недоступен. Попробуйте ещё раз."
          action={
            <Button variant="primary" onClick={() => void me.refetch()}>
              Повторить
            </Button>
          }
        />
      </AuthBoundaryState>
    );
  }

  return <AuthenticatedShell />;
}

/** Minimal branded frame for the resolution/error states — deliberately
 * without navigation, profile or any protected content. */
function AuthBoundaryState({ children }: { children: ReactNode }) {
  return (
    <main className={styles.boundary}>
      <BrandLogo height={96} />
      {children}
    </main>
  );
}

function AuthenticatedShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className={styles.shell}>
      <a href="#main-content" className="skip-link">
        Перейти к содержимому
      </a>
      <Header onOpenMobileNav={() => setMobileNavOpen(true)} />
      <Sidebar />
      <MobileNavDrawer open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <main className={styles.main} id="main-content">
        <Outlet />
      </main>
    </div>
  );
}
