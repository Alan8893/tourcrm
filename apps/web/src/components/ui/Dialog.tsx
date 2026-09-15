import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { IconButton } from "./Button";
import { useFocusTrap } from "./useFocusTrap";
import styles from "./Dialog.module.css";

export type DialogProps = {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children?: ReactNode;
  actions?: ReactNode;
};

/** Accessible modal dialog/confirmation primitive (spec §6): `role="dialog"`,
 * `aria-modal`, a focus trap that cycles Tab/Shift+Tab within the dialog,
 * initial focus moved in on open, Escape to close, and focus restored to
 * the element that opened the dialog on close (see useFocusTrap). */
export function Dialog({ open, title, description, onClose, children, actions }: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useFocusTrap(dialogRef, open, onClose);

  if (!open) return null;

  return createPortal(
    <div className={styles.overlay} onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className={styles.header}>
          <div>
            <h2 className={styles.title} id={titleId}>
              {title}
            </h2>
            {description ? (
              <p className={styles.description} id={descriptionId}>
                {description}
              </p>
            ) : null}
          </div>
          <IconButton icon="action.close" aria-label="Закрыть" onClick={onClose} />
        </div>
        {children}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
