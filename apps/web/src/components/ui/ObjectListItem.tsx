import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card } from "./Card";
import styles from "./ObjectListItem.module.css";

export type ObjectListItemProps = {
  title: string;
  to: string;
  status?: ReactNode;
  description?: string | null;
  actions?: ReactNode;
};

/**
 * A readable object-list row (spec §8: "prefer readable object lists over
 * dense enterprise tables when the task is discovery"). The title is the
 * only nested link — contextual `actions` are sibling buttons, not
 * nested inside the link, so the row never puts an interactive control
 * inside another one.
 */
export function ObjectListItem({ title, to, status, description, actions }: ObjectListItemProps) {
  return (
    <Card>
      <div className={styles.item}>
        <div className={styles.main}>
          <div className={styles.titleRow}>
            <h3 className={styles.title}>
              <Link to={to} className={styles.titleLink}>
                {title}
              </Link>
            </h3>
            {status}
          </div>
          {description ? <p className={styles.description}>{description}</p> : null}
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </Card>
  );
}
