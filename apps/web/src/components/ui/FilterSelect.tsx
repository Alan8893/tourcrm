import { useId } from "react";

import styles from "./Input.module.css";

export type FilterOption = {
  value: string;
  label: string;
};

export type FilterSelectProps = {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
};

/** A native `<select>` rather than a custom listbox: full keyboard/screen
 * reader support for free, no bespoke ARIA widget to get wrong, and it
 * still carries the TourCRM visual language via CSS (spec §6 "avoid
 * excessive component nesting"). */
export function FilterSelect({ label, value, options, onChange }: FilterSelectProps) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className={styles.select}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
