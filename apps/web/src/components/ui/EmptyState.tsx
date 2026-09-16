import type { ReactNode } from "react";

import { Illustration } from "./Icon";
import type { IllustrationId } from "../../assets/icons";
import styles from "./StatePage.module.css";

export type EmptyStateProps = {
  /** `empty-groups`/`empty-people`/... for "nothing created yet" states;
   * `no-results` is a distinct semantic state for "a search/filter
   * matched nothing" — the two must never collapse into one generic
   * illustration (ASSET-PACKS.md). */
  illustration: Extract<IllustrationId, "empty-groups" | "empty-people" | "no-results">;
  title: string;
  description?: string;
  action?: ReactNode;
};

export function EmptyState({ illustration, title, description, action }: EmptyStateProps) {
  return (
    <div className={styles.wrapper}>
      <Illustration id={illustration} size={128} />
      <h3 className={styles.title}>{title}</h3>
      {description ? <p className={styles.description}>{description}</p> : null}
      {action}
    </div>
  );
}
