import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError } from "./client";

/**
 * The authenticated user's own Telegram link and personal Telegram
 * notification preferences (Issue #343). Both APIs are self-scoped on the
 * backend (session-derived principal, CSRF on mutations, no RBAC):
 * docs/05-api/telegram-link-api.md and
 * docs/05-api/notification-preferences-api.md.
 *
 * No type here carries a Telegram numeric id, and no request sends one.
 * The one-time token exists only inside `deep_link` of the challenge
 * response; it is kept in component state only (never in the QueryCache or
 * MutationCache, see `createTelegramLinkChallenge`) and is never logged.
 */

export type TelegramLinkStatus = {
  linked: boolean;
  linked_at: string | null;
};

export type TelegramLinkChallenge = {
  deep_link: string;
  expires_at: string;
};

export type EventPreference = {
  event_type: string;
  /** Mandatory types (`event.cancelled`, `event.rescheduled`) ignore the
   * personal opt-out and are read-only. */
  mandatory: boolean;
  enabled: boolean;
};

export type NotificationPreferences = {
  channel: string;
  personal_enabled: boolean;
  telegram_linked: boolean;
  events: EventPreference[];
};

/** A partial update: an omitted field leaves the stored value unchanged. */
export type NotificationPreferencesUpdate = {
  personal_enabled?: boolean;
  events?: Record<string, boolean>;
};

const LINK_PATH = "/me/telegram-link";
const PREFERENCES_PATH = "/me/notification-preferences";

const keys = {
  link: ["me", "telegram-link"] as const,
  preferences: ["me", "notification-preferences"] as const,
};

/** A network failure rejects `fetch` with a non-`ApiError`; keep it typed
 * as an error the components can tell apart from an HTTP error. */
export type RequestError = ApiError | Error;

export function useTelegramLinkStatus({ pollWhilePending }: { pollWhilePending: boolean }) {
  return useQuery<TelegramLinkStatus, RequestError>({
    queryKey: keys.link,
    queryFn: () => apiFetch<TelegramLinkStatus>(LINK_PATH),
    retry: false,
    // While a one-time link is open, the bot may complete the link at any
    // moment: re-read the status so the page updates without a reload.
    refetchInterval: pollWhilePending ? 5000 : false,
  });
}

/** Re-read the link status and the preferences (whose `telegram_linked`
 * follows the link). */
export function useInvalidatePersonalTelegram() {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: keys.link });
    void queryClient.invalidateQueries({ queryKey: keys.preferences });
  }, [queryClient]);
}

/** Issue a one-time linking challenge. Deliberately a plain request, not a
 * `useMutation`: an observed mutation keeps its result (`state.data`) in
 * the shared MutationCache for as long as the component is mounted, and
 * `gcTime` only applies once no observer is left. The caller keeps the
 * response in component state for exactly as long as the link is valid. */
export function createTelegramLinkChallenge(): Promise<TelegramLinkChallenge> {
  return apiFetch<TelegramLinkChallenge>(`${LINK_PATH}/challenges`, { method: "POST" });
}

export function useUnlinkTelegram() {
  const invalidate = useInvalidatePersonalTelegram();
  return useMutation<void, RequestError, void>({
    mutationFn: () => apiFetch<void>(LINK_PATH, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useNotificationPreferences() {
  return useQuery<NotificationPreferences, RequestError>({
    queryKey: keys.preferences,
    queryFn: () => apiFetch<NotificationPreferences>(PREFERENCES_PATH),
    retry: false,
  });
}

export function useUpdateNotificationPreferences() {
  const queryClient = useQueryClient();
  return useMutation<NotificationPreferences, RequestError, NotificationPreferencesUpdate>({
    mutationFn: (payload) =>
      apiFetch<NotificationPreferences>(PREFERENCES_PATH, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    // The backend returns the full, authoritative state after the update.
    onSuccess: (saved) => queryClient.setQueryData(keys.preferences, saved),
  });
}
