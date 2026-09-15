import { NavList } from "./NavList";
import styles from "./Sidebar.module.css";

/** Desktop: persistent full-width sidebar. Tablet: the same component,
 * compacted to icon-only by Sidebar.module.css alone (see its comment) —
 * one composition, not two. Hidden below the mobile breakpoint in favor
 * of MobileNavDrawer. */
export function Sidebar() {
  return (
    <nav aria-label="Основная навигация" className={styles.sidebar}>
      <NavList />
    </nav>
  );
}
