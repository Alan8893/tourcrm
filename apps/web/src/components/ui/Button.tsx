import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Icon } from "./Icon";
import type { IconId } from "../../assets/icons";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "secondary" | "destructive";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** Action hierarchy per UI-FOUNDATION-SPEC.md §7: primary (main action
   * in context), secondary (useful, non-primary), destructive
   * (consequential/irreversible). Never make several local actions look
   * as prominent as the primary one. */
  variant?: ButtonVariant;
  icon?: IconId;
  children: ReactNode;
};

export function Button({ variant = "secondary", icon, className, children, ...rest }: ButtonProps) {
  return (
    <button className={`${styles.button} ${styles[variant]} ${className ?? ""}`} {...rest}>
      {icon ? <Icon id={icon} size={20} /> : null}
      {children}
    </button>
  );
}

export type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: IconId;
  /** Required, not optional: an icon-only control must always carry an
   * accessible name (spec §12) — there is no visible text to fall back
   * on. */
  "aria-label": string;
};

export function IconButton({ icon, className, ...rest }: IconButtonProps) {
  return (
    <button className={`${styles.iconButton} ${className ?? ""}`} {...rest}>
      <Icon id={icon} size={20} />
    </button>
  );
}
