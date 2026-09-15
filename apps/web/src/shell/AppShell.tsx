import { useState } from "react";
import { Outlet } from "react-router-dom";

import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { MobileNavDrawer } from "./MobileNavDrawer";
import styles from "./AppShell.module.css";

/** The shared App Shell (spec §3): brand/header zone, persistent
 * navigation (Sidebar on desktop/tablet, a drawer on mobile) and a main
 * content area rendered once here and reused by every page via
 * `<Outlet />` — no page re-implements its own header/nav. */
export function AppShell() {
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
