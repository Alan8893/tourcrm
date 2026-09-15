import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Avatar } from "../components/ui/Avatar";
import { Icon } from "../components/ui/Icon";
import { useCurrentUser, displayName } from "../api/auth";
import styles from "./ProfileMenu.module.css";

/**
 * The App Shell's only entry point to profile/settings (spec §3: "Do not
 * add a separate Settings icon to the header"; §7 "profile/settings
 * access uses the avatar/profile area"). No login screen exists yet in
 * this Issue's scope, so an unauthenticated visitor sees a neutral
 * "Гость" state rather than a crash or fabricated identity.
 */
export function ProfileMenu() {
  const { data, isError } = useCurrentUser();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();

  const name = data ? displayName(data.user) : undefined;
  const label = name ?? "Гость";

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div className={styles.wrapper} ref={wrapperRef}>
      <button
        type="button"
        className={styles.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((value) => !value)}
      >
        <Avatar name={isError ? undefined : name} />
        <span className={styles.name}>{label}</span>
      </button>
      {open ? (
        <div className={styles.menu} role="menu" id={menuId} aria-label="Профиль">
          <div className={styles.menuHeading}>{label}</div>
          <Link
            to="/settings"
            role="menuitem"
            className={styles.menuItem}
            onClick={() => setOpen(false)}
          >
            <Icon id="nav.settings" size={20} />
            Настройки
          </Link>
        </div>
      ) : null}
    </div>
  );
}
