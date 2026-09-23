import { Link } from "react-router-dom";

import { BrandLogo } from "../components/ui/Icon";
import { ProfileMenu } from "./ProfileMenu";
import { HeaderScene } from "./HeaderScene";
import styles from "./Header.module.css";

export type HeaderProps = {
  onOpenMobileNav: () => void;
};

/** Top brand/header zone (compact single row) with the approved decorative
 * daily illustration as a restrained, secondary accent (TH-0089). Desktop
 * and mobile use distinct compositions via Header.module.css /
 * HeaderScene.module.css — the mobile header is not a scaled-down desktop. */
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
        <Link to="/" className={styles.brand} aria-label="TourCRM — на главную">
          <BrandLogo height={68} className={styles.logo} />
        </Link>
      </div>
      <div className={styles.profile}>
        <ProfileMenu />
      </div>
    </header>
  );
}
