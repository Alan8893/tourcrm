import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * Settings → Notifications (Issue #333, ADR-0048;
 * docs/05-api/notification-settings-api.md). Authorization is the
 * backend's: `notification.manage` (policy, rules, status, test send) and
 * `settings.manage` (integrations, secrets).
 *
 * Secrets are write-only: no response type here has a field for a secret
 * value — only `password_configured` / `bot_token_configured`. A secret is
 * sent only by the explicit replace mutations, never as part of the other
 * settings, and the mutation variables are dropped from the cache once the
 * request settles (see the components).
 */

const BASE = "/settings/notifications";

export type ConfigurationState =
  | "not_configured"
  | "incomplete"
  | "invalid"
  | "secret_unavailable"
  | "configured";
export type EncryptionState = "available" | "missing" | "invalid";
export type SmtpSecurity = "starttls" | "ssl" | "none";

export type NotificationPolicy = {
  email_enabled: boolean;
  telegram_enabled: boolean;
  saved: boolean;
};

export type NotificationRule = {
  id: string;
  event_type: string;
  channel: string;
  recipient_scope: string;
  is_enabled: boolean;
};

export type ChannelStatus = {
  policy_enabled: boolean;
  configuration: ConfigurationState;
  ready: boolean;
};

export type NotificationStatus = {
  encryption: EncryptionState;
  email: ChannelStatus;
  telegram: ChannelStatus & { linking_available: boolean };
};

export type EmailSettings = {
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_security: SmtpSecurity;
  smtp_username: string | null;
  sender_email: string | null;
  sender_name: string | null;
  password_configured: boolean;
};

export type EmailSettingsInput = Omit<EmailSettings, "password_configured">;

export type TelegramSettings = {
  bot_username: string | null;
  bot_token_configured: boolean;
};

export type Integrations = {
  encryption: EncryptionState;
  email: EmailSettings;
  telegram: TelegramSettings;
};

export type TelegramDestinationOption = {
  id: string;
  name: string;
  topic_name: string | null;
};

export type TestSendInput =
  | { channel: "email"; destination_kind: "email_address"; email: string }
  | { channel: "telegram"; destination_kind: "own_telegram_account" }
  | {
      channel: "telegram";
      destination_kind: "telegram_destination";
      telegram_destination_id: string;
    };

export type TestSendResult = {
  test: true;
  channel: "email" | "telegram";
  destination_kind: string;
  status: "delivered" | "failed";
  error_code: string | null;
};

export type SecretName = "smtp_password" | "telegram_bot_token";

const SECRET_PATH: Record<SecretName, string> = {
  smtp_password: "/integrations/email/password",
  telegram_bot_token: "/integrations/telegram/bot-token",
};

const keys = {
  policy: ["settings", "notifications", "policy"] as const,
  rules: ["settings", "notifications", "rules"] as const,
  status: ["settings", "notifications", "status"] as const,
  integrations: ["settings", "notifications", "integrations"] as const,
  destinations: ["settings", "notifications", "telegram-destinations"] as const,
};

export function useNotificationPolicy() {
  return useQuery<NotificationPolicy, ApiError>({
    queryKey: keys.policy,
    queryFn: () => apiFetch<NotificationPolicy>(`${BASE}/policy`),
    retry: false,
  });
}

export function useNotificationStatus() {
  return useQuery<NotificationStatus, ApiError>({
    queryKey: keys.status,
    queryFn: () => apiFetch<NotificationStatus>(`${BASE}/status`),
    retry: false,
  });
}

export function useNotificationRules() {
  return useQuery<CollectionResponse<NotificationRule>, ApiError>({
    queryKey: keys.rules,
    queryFn: () => apiFetch<CollectionResponse<NotificationRule>>(`${BASE}/rules`),
    retry: false,
  });
}

export function useNotificationIntegrations() {
  return useQuery<Integrations, ApiError>({
    queryKey: keys.integrations,
    queryFn: () => apiFetch<Integrations>(`${BASE}/integrations`),
    retry: false,
  });
}

export function useTestTelegramDestinations() {
  return useQuery<CollectionResponse<TelegramDestinationOption>, ApiError>({
    queryKey: keys.destinations,
    queryFn: () =>
      apiFetch<CollectionResponse<TelegramDestinationOption>>(`${BASE}/telegram-destinations`),
    retry: false,
  });
}

function useInvalidateNotificationSettings() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["settings", "notifications"] });
  };
}

export function useUpdateNotificationPolicy() {
  const invalidate = useInvalidateNotificationSettings();
  return useMutation<
    NotificationPolicy,
    ApiError,
    Pick<NotificationPolicy, "email_enabled" | "telegram_enabled">
  >({
    mutationFn: (payload) =>
      apiFetch<NotificationPolicy>(`${BASE}/policy`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: invalidate,
  });
}

export function useUpdateNotificationRule() {
  const invalidate = useInvalidateNotificationSettings();
  return useMutation<NotificationRule, ApiError, { id: string; is_enabled: boolean }>({
    mutationFn: ({ id, is_enabled }) =>
      apiFetch<NotificationRule>(`${BASE}/rules/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_enabled }),
      }),
    onSuccess: invalidate,
  });
}

export function useUpdateEmailSettings() {
  const invalidate = useInvalidateNotificationSettings();
  return useMutation<EmailSettings, ApiError, EmailSettingsInput>({
    mutationFn: (payload) =>
      apiFetch<EmailSettings>(`${BASE}/integrations/email`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: invalidate,
  });
}

export function useUpdateTelegramSettings() {
  const invalidate = useInvalidateNotificationSettings();
  return useMutation<TelegramSettings, ApiError, { bot_username: string | null }>({
    mutationFn: (payload) =>
      apiFetch<TelegramSettings>(`${BASE}/integrations/telegram`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    onSuccess: invalidate,
  });
}

const secretMutationKey = (name: SecretName) =>
  ["settings", "notifications", "secret", name] as const;

/** Set or replace a secret. The value is sent once and must not outlive the
 * request on the client: the mutation has its own key and `gcTime: 0`, and
 * `discard()` — to be called as soon as the request settles, whatever its
 * outcome — resets the observer AND removes every settled mutation of this
 * key from the MutationCache. (`reset()` alone only detaches the observer;
 * TanStack Query keeps the mutation, with its `variables`, until gcTime.)
 * No other mutation or cache entry is touched. */
export function useReplaceSecret(name: SecretName) {
  const queryClient = useQueryClient();
  const invalidate = useInvalidateNotificationSettings();
  const mutation = useMutation<{ configured: boolean }, ApiError, string>({
    mutationKey: secretMutationKey(name),
    gcTime: 0,
    mutationFn: (value) =>
      apiFetch<{ configured: boolean }>(`${BASE}${SECRET_PATH[name]}`, {
        method: "PUT",
        body: JSON.stringify({ value }),
      }),
    onSuccess: invalidate,
  });
  const { reset } = mutation;
  const discard = useCallback(() => {
    reset();
    const cache = queryClient.getMutationCache();
    for (const settled of cache.findAll({ mutationKey: secretMutationKey(name), exact: true })) {
      if (settled.state.status !== "pending") cache.remove(settled);
    }
  }, [name, queryClient, reset]);
  return { mutation, discard };
}

export function useClearSecret(name: SecretName) {
  const invalidate = useInvalidateNotificationSettings();
  return useMutation<void, ApiError, void>({
    mutationFn: () => apiFetch<void>(`${BASE}${SECRET_PATH[name]}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useTestSend() {
  return useMutation<TestSendResult, ApiError, TestSendInput>({
    mutationFn: (payload) =>
      apiFetch<TestSendResult>(`${BASE}/test-send`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}
