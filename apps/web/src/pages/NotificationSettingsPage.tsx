import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import {
  useNotificationIntegrations,
  useNotificationPolicy,
  useNotificationRules,
  useNotificationStatus,
  useUpdateNotificationPolicy,
  useUpdateNotificationRule,
  type ChannelStatus,
  type NotificationPolicy,
  type NotificationRule,
} from "../api/notificationSettings";
import {
  CONFIGURATION_LABEL,
  ENCRYPTION_MESSAGE,
} from "../domain/notificationSettingsText";
import { EmailSettingsForm, TelegramSettingsForm } from "./NotificationIntegrationForms";
import { NotificationTestSend } from "./NotificationTestSend";
import styles from "./NotificationSettingsPage.module.css";

const CHANNEL_LABEL: Record<string, string> = { email: "Email", telegram: "Telegram" };

function SectionError({ status, onRetry }: { status?: number; onRetry: () => void }) {
  if (status === 403) {
    return <p className={styles.note}>Этот раздел вам недоступен.</p>;
  }
  return (
    <div className={styles.loadError}>
      <p className={styles.error} role="alert">
        Не удалось загрузить данные.
      </p>
      <Button variant="secondary" onClick={onRetry}>
        Повторить
      </Button>
    </div>
  );
}

function ReadinessBadge({ status }: { status: ChannelStatus }) {
  if (status.ready) return <StatusBadge status="status.success" label="Готов к отправке" />;
  if (!status.policy_enabled) return <StatusBadge status="status.ended" label="Выключен" />;
  return <StatusBadge status="status.warning" label={CONFIGURATION_LABEL[status.configuration]} />;
}

function PolicySection() {
  const policy = useNotificationPolicy();
  const status = useNotificationStatus();
  const update = useUpdateNotificationPolicy();
  const notify = useNotify();
  const [error, setError] = useState<string | null>(null);

  if (policy.isLoading || status.isLoading) return <Loading label="Загружаем политику…" />;
  if (policy.isError || !policy.data) {
    return <SectionError status={policy.error?.status} onRetry={() => void policy.refetch()} />;
  }

  function toggle(channel: "email" | "telegram", current: NotificationPolicy) {
    setError(null);
    update.mutate(
      {
        email_enabled: channel === "email" ? !current.email_enabled : current.email_enabled,
        telegram_enabled:
          channel === "telegram" ? !current.telegram_enabled : current.telegram_enabled,
      },
      {
        onSuccess: (saved) =>
          notify(
            "success",
            `${CHANNEL_LABEL[channel]}: ${
              (channel === "email" ? saved.email_enabled : saved.telegram_enabled)
                ? "включён"
                : "выключен"
            }`,
          ),
        onError: () => setError("Не удалось сохранить политику. Попробуйте ещё раз."),
      },
    );
  }

  const current = policy.data;
  return (
    <div className={styles.form}>
      {!current.saved ? (
        <p className={styles.note}>
          Политика ещё не сохранялась: пока все каналы выключены и уведомления не отправляются.
        </p>
      ) : null}
      <ul className={styles.channelList}>
        {(["email", "telegram"] as const).map((channel) => {
          const enabled = channel === "email" ? current.email_enabled : current.telegram_enabled;
          const channelStatus = status.data?.[channel];
          return (
            <li key={channel} className={styles.channelRow}>
              <label className={styles.switch}>
                <input
                  type="checkbox"
                  role="switch"
                  checked={enabled}
                  disabled={update.isPending}
                  onChange={() => toggle(channel, current)}
                />
                <span>{CHANNEL_LABEL[channel]}</span>
              </label>
              {channelStatus ? <ReadinessBadge status={channelStatus} /> : null}
            </li>
          );
        })}
      </ul>
      <p className={styles.note}>
        Выключенный канал блокирует отправку всех уведомлений по нему, в том числе уже
        запланированных. Выключение Telegram не отключает привязку аккаунтов Telegram.
      </p>
      {status.data && status.data.encryption !== "available" ? (
        <p className={styles.error} role="alert">
          {ENCRYPTION_MESSAGE[status.data.encryption]}
        </p>
      ) : null}
      {status.data && !status.data.telegram.linking_available ? (
        <p className={styles.note}>
          Привязка Telegram-аккаунтов станет доступна после указания username бота.
        </p>
      ) : null}
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function RuleRow({ rule }: { rule: NotificationRule }) {
  const update = useUpdateNotificationRule();
  const notify = useNotify();
  return (
    <li className={styles.channelRow}>
      <label className={styles.switch}>
        <input
          type="checkbox"
          role="switch"
          checked={rule.is_enabled}
          disabled={update.isPending}
          onChange={() =>
            update.mutate(
              { id: rule.id, is_enabled: !rule.is_enabled },
              {
                onSuccess: () => notify("success", "Правило обновлено"),
                onError: () => notify("error", "Не удалось обновить правило"),
              },
            )
          }
        />
        <span>
          <code>{rule.event_type}</code> · {CHANNEL_LABEL[rule.channel] ?? rule.channel} ·{" "}
          {rule.recipient_scope}
        </span>
      </label>
    </li>
  );
}

function RulesSection() {
  const rules = useNotificationRules();
  if (rules.isLoading) return <Loading label="Загружаем правила…" />;
  if (rules.isError || !rules.data) {
    return <SectionError status={rules.error?.status} onRetry={() => void rules.refetch()} />;
  }
  if (rules.data.items.length === 0) {
    return (
      <p className={styles.note}>
        Правил пока нет. Правило появляется, когда событие проходит согласование (specification
        gate); здесь существующие правила можно только включать и выключать.
      </p>
    );
  }
  return (
    <ul className={styles.channelList}>
      {rules.data.items.map((rule) => (
        <RuleRow key={rule.id} rule={rule} />
      ))}
    </ul>
  );
}

function IntegrationsSection({ channel }: { channel: "email" | "telegram" }) {
  const integrations = useNotificationIntegrations();
  if (integrations.isLoading) return <Loading label="Загружаем настройки…" />;
  if (integrations.isError || !integrations.data) {
    return (
      <SectionError
        status={integrations.error?.status}
        onRetry={() => void integrations.refetch()}
      />
    );
  }
  const data = integrations.data;
  const secretsWritable = data.encryption === "available";
  return channel === "email" ? (
    <EmailSettingsForm
      key={JSON.stringify(data.email)}
      settings={data.email}
      secretsWritable={secretsWritable}
    />
  ) : (
    <TelegramSettingsForm
      key={JSON.stringify(data.telegram)}
      settings={data.telegram}
      secretsWritable={secretsWritable}
    />
  );
}

/**
 * Settings → Notifications (Issue #333, ADR-0048). Administrator-only by
 * route guard for UX; every section's data comes from endpoints the
 * backend authorizes on its own (`notification.manage` /
 * `settings.manage`), so a section the backend denies shows a note
 * instead of controls.
 */
export function NotificationSettingsPage() {
  return (
    <div>
      <PageHeader title="Уведомления" />
      <p className={styles.back}>
        <Link to="/settings">← Настройки</Link>
      </p>
      <Card className={styles.card}>
        <h2 className={styles.sectionTitle}>Общие</h2>
        <PolicySection />
      </Card>
      <Card className={styles.card}>
        <h2 className={styles.sectionTitle}>Правила уведомлений</h2>
        <RulesSection />
      </Card>
      <Card className={styles.card}>
        <h2 className={styles.sectionTitle}>Email</h2>
        <IntegrationsSection channel="email" />
      </Card>
      <Card className={styles.card}>
        <h2 className={styles.sectionTitle}>Telegram</h2>
        <IntegrationsSection channel="telegram" />
      </Card>
      <Card className={styles.card}>
        <h2 className={styles.sectionTitle}>Тестовое сообщение</h2>
        <NotificationTestSend />
      </Card>
    </div>
  );
}
