import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Avatar } from "../components/ui/Avatar";
import { Icon } from "../components/ui/Icon";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser, useLogout, displayName } from "../api/auth";
import { currentUserPhotoUrl } from "../api/profilePhoto";
import styles from "./ProfileMenu.module.css";

/**
 * The App Shell's only entry point to profile/settings (spec §3: "Do not
 * add a separate Settings icon to the header"; §7 "profile/settings
 * access uses the avatar/profile area"). Rendered only inside the
 * authenticated shell (TH-0089 / spec §3.1): AppShell gates on `/auth/me`,
 * so there is no Guest pseudo-profile — without a resolved identity this
 * renders nothing rather than a placeholder name.
 */
export function ProfileMenu() {
  const { data } = useCurrentUser();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const navigate = useNavigate();
  const notify = useNotify();
  const logout = useLogout();

  function handleLogout() {
    setOpen(false);
    logout.mutate(undefined, {
      onSuccess: () => navigate("/login"),
      onError: (error) => notify("error", error.message),
    });
  }

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

  if (!data) return null;
  const label = displayName(data.user);

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
        <Avatar name={label} photoUrl={currentUserPhotoUrl(data)} />
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
          <button
            type="button"
            role="menuitem"
            className={styles.menuItem}
            disabled={logout.isPending}
            onClick={handleLogout}
          >
            Выйти
          </button>
        </div>
      ) : null}
    </div>
  );
}
