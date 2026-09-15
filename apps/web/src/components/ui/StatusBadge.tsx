import { Icon } from "./Icon";
import type { StatusIconId } from "../../assets/icons";
import styles from "./StatusBadge.module.css";

export type StatusBadgeProps = {
  status: StatusIconId;
  label: string;
};

/** Icon + visible text always together — status is never communicated by
 * color alone (spec §12 "no color-only semantics"). `status` must be one
 * of the nine approved, closed status meanings
 * (docs/06-ui/assets/ASSET-STATUS.md); domain status values are mapped to
 * these onto meaning, never onto string equality — see
 * src/domain/statusMapping.ts. */
export function StatusBadge({ status, label }: StatusBadgeProps) {
  const tone = status.replace("status.", "") as
    | "planned"
    | "ongoing"
    | "completed"
    | "ended"
    | "archived"
    | "success"
    | "warning"
    | "error"
    | "info";
  return (
    <span className={`${styles.badge} ${styles[tone]}`}>
      <Icon id={status} size={16} />
      {label}
    </span>
  );
}
