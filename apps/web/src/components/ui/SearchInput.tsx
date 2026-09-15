import { useId } from "react";

import { Icon } from "./Icon";
import styles from "./Input.module.css";

export type SearchInputProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

export function SearchInput({ label, value, onChange, placeholder }: SearchInputProps) {
  const inputId = useId();
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={inputId}>
        {label}
      </label>
      <div className={styles.searchWrapper}>
        <span className={styles.searchIcon}>
          <Icon id="action.search" size={20} />
        </span>
        <input
          id={inputId}
          type="search"
          className={styles.input}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
        {value ? (
          <button
            type="button"
            className={styles.clearButton}
            aria-label="Очистить поиск"
            onClick={() => onChange("")}
          >
            <Icon id="action.close" size={16} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
