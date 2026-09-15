import { NavLink } from "react-router-dom";

import { Icon } from "../components/ui/Icon";
import { NAVIGATION_ITEMS } from "./navigation";
import styles from "./NavList.module.css";

export type NavListProps = {
  onNavigate?: () => void;
};

/** The single source of the seven-item navigation markup, shared by the
 * desktop/tablet Sidebar and the mobile navigation drawer (spec §6:
 * "avoid duplicated shell logic between pages"). `NavLink` sets
 * `aria-current="page"` on the active entry automatically.
 *
 * The label carries the plain `nav-label` class (in addition to the CSS
 * module class) purely so the Sidebar's own stylesheet can visually hide
 * it at the tablet breakpoint (`Sidebar.module.css`) while it stays in
 * the DOM for assistive tech — no `compact` prop/viewport check needed
 * here, the same markup adapts through CSS alone. */
export function NavList({ onNavigate }: NavListProps) {
  return (
    <ul className={styles.list}>
      {NAVIGATION_ITEMS.map((item) => (
        <li key={item.id}>
          <NavLink
            to={item.path}
            end={item.path === "/"}
            className={({ isActive }) => `${styles.link} ${isActive ? styles.linkActive : ""}`}
            onClick={onNavigate}
          >
            <Icon id={item.icon} size={24} />
            <span className={`${styles.label} nav-label`}>{item.label}</span>
          </NavLink>
        </li>
      ))}
    </ul>
  );
}
