import styles from "./Stepper.module.css";

export type StepperProps = {
  /** Accessible name of the step list, e.g. "Шаги импорта". */
  label: string;
  steps: readonly string[];
  /** Zero-based index of the current step. */
  current: number;
};

/** Read-only wizard progress indicator: an ordered list where the current
 * step carries `aria-current="step"` and completed steps are marked in
 * text, not by color alone (spec §12). Wraps on narrow screens. */
export function Stepper({ label, steps, current }: StepperProps) {
  return (
    <ol className={styles.stepper} aria-label={label}>
      {steps.map((step, index) => {
        const state = index < current ? "done" : index === current ? "current" : "upcoming";
        return (
          <li
            key={step}
            className={`${styles.step} ${styles[state]}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className={styles.index} aria-hidden="true">
              {state === "done" ? "✓" : index + 1}
            </span>
            <span className={styles.label}>
              {step}
              {state === "done" ? <span className={styles.srOnly}> (выполнено)</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
