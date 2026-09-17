import { Link } from "react-router-dom";

import { BrandLogo } from "../components/ui/Icon";
import { ProfileMenu } from "./ProfileMenu";
import { HeaderScene } from "./HeaderScene";
import styles from "./Header.module.css";

export type HeaderProps = {
  onOpenMobileNav: () => void;
};

/** Top brand/header zone with the approved decorative daily illustration. */
export function Header({ onOpenMobileNav }: HeaderProps) {
  return (
    <header className={styles.header}>
      <HeaderScene />
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
      <div className={styles.profile}>
        <ProfileMenu />
      </div>
    </header>
  );
}
