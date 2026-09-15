import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import styles from "./Tabs.module.css";

export type TabItem = {
  id: string;
  label: string;
  content: ReactNode;
};

export type TabsProps = {
  label: string;
  items: TabItem[];
  activeId: string;
  onChange: (id: string) => void;
};

/** Full WAI-ARIA "tabs" pattern: roving tabindex (only the selected tab is
 * in the Tab order; Left/Right/Home/End move focus and activate),
 * `aria-selected`, and `role="tabpanel"` linked via `aria-labelledby` —
 * spec §9 "Active state must be clear and keyboard accessible." */
export function Tabs({ label, items, activeId, onChange }: TabsProps) {
  const baseId = useId();
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const activeIndex = items.findIndex((item) => item.id === activeId);

  function focusAndSelect(index: number) {
    const item = items[(index + items.length) % items.length];
    if (!item) return;
    onChange(item.id);
    tabRefs.current[item.id]?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        focusAndSelect(activeIndex + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        focusAndSelect(activeIndex - 1);
        break;
      case "Home":
        event.preventDefault();
        focusAndSelect(0);
        break;
      case "End":
        event.preventDefault();
        focusAndSelect(items.length - 1);
        break;
      default:
        break;
    }
  }

  const activeItem = items[activeIndex];

  return (
    <div>
      <div role="tablist" aria-label={label} className={styles.tablist}>
        {items.map((item) => {
          const selected = item.id === activeId;
          return (
            <button
              key={item.id}
              ref={(node) => {
                tabRefs.current[item.id] = node;
              }}
              role="tab"
              id={`${baseId}-tab-${item.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.id}`}
              tabIndex={selected ? 0 : -1}
              className={`${styles.tab} ${selected ? styles.tabSelected : ""}`}
              onClick={() => onChange(item.id)}
              onKeyDown={handleKeyDown}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {activeItem ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${activeItem.id}`}
          aria-labelledby={`${baseId}-tab-${activeItem.id}`}
          tabIndex={0}
          className={styles.panel}
        >
          {activeItem.content}
        </div>
      ) : null}
    </div>
  );
}
