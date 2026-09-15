import { Link } from "react-router-dom";

import { BrandLogo } from "../components/ui/Icon";
import { ProfileMenu } from "./ProfileMenu";
import styles from "./Header.module.css";

export type HeaderProps = {
  onOpenMobileNav: () => void;
};

/** Top brand/header zone (spec §3): official logo on the left, avatar/
 * profile on the right, present identically on every breakpoint. The
 * mobile menu control only renders (via CSS) below the mobile breakpoint,
 * where Sidebar is hidden in favor of MobileNavDrawer. A plain text
 * label ("Меню") rather than an icon: the approved Actions/Navigation
 * catalogs have no "menu/hamburger" concept, and visible text avoids
 * either inventing new artwork or repurposing an unrelated approved icon
 * (e.g. "more") for a different meaning. */
export function Header({ onOpenMobileNav }: HeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.start}>
        <button
          type="button"
          className={styles.menuButton}
          aria-haspopup="dialog"
          onClick={onOpenMobileNav}
        >
          Меню
        </button>
        <Link to="/" aria-label="TourCRM — на главную">
          <BrandLogo />
        </Link>
      </div>
      <ProfileMenu />
    </header>
  );
}
