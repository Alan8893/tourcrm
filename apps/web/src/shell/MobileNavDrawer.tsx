import { useRef } from "react";
import { createPortal } from "react-dom";

import { BrandLogo } from "../components/ui/Icon";
import { Button } from "../components/ui/Button";
import { useFocusTrap } from "../components/ui/useFocusTrap";
import { NavList } from "./NavList";
import styles from "./MobileNavDrawer.module.css";

export type MobileNavDrawerProps = {
  open: boolean;
  onClose: () => void;
};

/** The mobile "menu/navigation control" from spec §3: all seven
 * navigation items, always present and reachable, in a full-height
 * drawer rather than a partial bottom-nav subset (spec allows bottom
 * navigation only "when it improves access" — it is not mandatory, and a
 * bottom bar can only fit a handful of the seven approved items without
 * either dropping some or introducing a second, different navigation
 * list to maintain). */
export function MobileNavDrawer({ open, onClose }: MobileNavDrawerProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  useFocusTrap(panelRef, open, onClose);

  if (!open) return null;

  return createPortal(
    <div
      className={styles.overlay}
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label="Навигация"
        ref={panelRef}
        tabIndex={-1}
      >
        <div className={styles.header}>
          <BrandLogo height={96} className={styles.logo} />
          <Button
            icon="action.close"
            aria-label="Закрыть меню"
            className={styles.close}
            onClick={onClose}
          >
            Закрыть
          </Button>
        </div>
        <NavList onNavigate={onClose} />
      </div>
    </div>,
    document.body,
  );
}
