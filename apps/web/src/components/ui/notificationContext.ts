import { createContext, useContext } from "react";

export type NotificationTone = "success" | "error" | "info";

export type NotificationItem = {
  id: string;
  tone: NotificationTone;
  message: string;
};

export type NotifyFn = (tone: NotificationTone, message: string) => void;

export const NotificationContext = createContext<NotifyFn | null>(null);

export function useNotify(): NotifyFn {
  const notify = useContext(NotificationContext);
  if (!notify) {
    throw new Error("useNotify must be used within a NotificationProvider");
  }
  return notify;
}
