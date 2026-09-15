import styles from "./Loading.module.css";

export type LoadingProps = {
  label?: string;
};

/** `role="status"` announces the label to assistive tech without moving
 * focus; the spinner itself is purely decorative. */
export function Loading({ label = "Загрузка…" }: LoadingProps) {
  return (
    <div className={styles.wrapper} role="status">
      <span className={styles.spinner} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
