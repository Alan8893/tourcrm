import type { ReactNode } from "react";

import { Illustration } from "./Icon";
import type { IllustrationId } from "../../assets/icons";
import styles from "./StatePage.module.css";

export type ErrorStateProps = {
  illustration: Extract<IllustrationId, "error" | "403" | "404">;
  title: string;
  description?: string;
  action?: ReactNode;
};

/** System-state illustration wrapper — a real 401/403 from the backend,
 * an unresolved route, or an unexpected failure, never a domain empty
 * list (that is `EmptyState`; ASSET-PACKS.md keeps the two families
 * distinct). Rendered with `role="alert"` so assistive tech announces it
 * immediately. */
export function ErrorState({ illustration, title, description, action }: ErrorStateProps) {
  return (
    <div className={styles.wrapper} role="alert">
      <Illustration id={illustration} size={128} />
      <h3 className={styles.title}>{title}</h3>
      {description ? <p className={styles.description}>{description}</p> : null}
      {action}
    </div>
  );
}
