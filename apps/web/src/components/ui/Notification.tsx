import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { IconButton } from "./Button";
import { Icon } from "./Icon";
import type { StatusIconId } from "../../assets/icons";
import { NotificationContext, type NotificationItem, type NotifyFn } from "./notificationContext";
import styles from "./Notification.module.css";

const TONE_ICON: Record<NotificationItem["tone"], StatusIconId> = {
  success: "status.success",
  error: "status.error",
  info: "status.info",
};

const AUTO_DISMISS_MS = 6000;

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const dismiss = useCallback((id: string) => {
    setItems((current) => current.filter((item) => item.id !== id));
    clearTimeout(timers.current[id]);
    delete timers.current[id];
  }, []);

  const notify = useCallback<NotifyFn>(
    (tone, message) => {
      const id = crypto.randomUUID();
      setItems((current) => [...current, { id, tone, message }]);
      timers.current[id] = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );

  useEffect(() => {
    const activeTimers = timers.current;
    return () => {
      Object.values(activeTimers).forEach(clearTimeout);
    };
  }, []);

  return (
    <NotificationContext.Provider value={notify}>
      {children}
      {/* `aria-live="polite"`: announced without interrupting the current
       * screen-reader focus (spec §12 accessibility baseline). */}
      <div className={styles.region} role="status" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`${styles.toast} ${styles[item.tone]}`}>
            <Icon id={TONE_ICON[item.tone]} size={20} />
            <span className={styles.message}>{item.message}</span>
            <IconButton
              icon="action.close"
              aria-label="Скрыть уведомление"
              onClick={() => dismiss(item.id)}
            />
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  );
}
