import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Icon } from "./Icon";
import styles from "./PageHeader.module.css";

export type PageHeaderProps = {
  title: string;
  description?: string;
  /** "Where am I?" — spec §4. Omit on top-level pages that need no
   * return context (e.g. Home). */
  back?: { to: string; label: string };
  titleExtra?: ReactNode;
  actions?: ReactNode;
};

/** Common page structure (spec §4): context/back, title, short
 * description, primary action — not every page needs every element. */
export function PageHeader({ title, description, back, titleExtra, actions }: PageHeaderProps) {
  return (
    <div className={styles.wrapper}>
      {back ? (
        <Link to={back.to} className={styles.breadcrumb}>
          <Icon id="action.back" size={16} />
          {back.label}
        </Link>
      ) : null}
      <div className={styles.row}>
        <div>
          <div className={styles.titleGroup}>
            <h1 className={styles.title}>{title}</h1>
            {titleExtra}
          </div>
          {description ? <p className={styles.description}>{description}</p> : null}
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </div>
  );
}
