import { useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { FilterSelect } from "../components/ui/FilterSelect";
import { Input } from "../components/ui/Input";
import { StatusBadge } from "../components/ui/StatusBadge";
import { ApiError } from "../api/client";
import {
  useTestSend,
  useTestTelegramDestinations,
  type TestSendInput,
  type TestSendResult,
} from "../api/notificationSettings";
import { testSendErrorMessage } from "../domain/notificationSettingsText";
import styles from "./NotificationSettingsPage.module.css";

const OWN_ACCOUNT = "own";

function requestErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  if (error.status === 429) {
    return "Слишком много тестовых сообщений. Подождите несколько минут и попробуйте снова.";
  }
  if (error.code === "invalid_email") return "Укажите корректный адрес электронной почты.";
  if (error.status === 422) return "Выберите получателя тестового сообщения.";
  if (error.status === 403) return "Недостаточно прав для отправки тестового сообщения.";
  return "Не удалось отправить тестовое сообщение.";
}

/**
 * Explicit test send (ADR-0048 §2.4/§2.10): Email to an entered address;
 * Telegram to the administrator's own linked account or an existing
 * enabled destination — never a free-form chat id. The result is labelled
 * as a test; nothing here creates a notification.
 */
export function NotificationTestSend() {
  const [channel, setChannel] = useState<"email" | "telegram">("email");
  const [email, setEmail] = useState("");
  const [telegramTarget, setTelegramTarget] = useState(OWN_ACCOUNT);
  const [result, setResult] = useState<TestSendResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = useTestSend();
  const destinations = useTestTelegramDestinations();

  const telegramOptions = [
    { value: OWN_ACCOUNT, label: "Мой Telegram-аккаунт" },
    ...(destinations.data?.items ?? []).map((item) => ({
      value: item.id,
      label: item.topic_name ? `${item.name} — ${item.topic_name}` : item.name,
    })),
  ];

  function reset() {
    setResult(null);
    setError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (send.isPending) return;
    reset();
    let payload: TestSendInput;
    if (channel === "email") {
      if (!email.trim()) {
        setError("Укажите адрес электронной почты для тестового сообщения.");
        return;
      }
      payload = { channel, destination_kind: "email_address", email: email.trim() };
    } else if (telegramTarget === OWN_ACCOUNT) {
      payload = { channel, destination_kind: "own_telegram_account" };
    } else {
      payload = {
        channel,
        destination_kind: "telegram_destination",
        telegram_destination_id: telegramTarget,
      };
    }
    send.mutate(payload, {
      onSuccess: (response) => setResult(response),
      onError: (mutationError) => setError(requestErrorMessage(mutationError)),
    });
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate aria-label="Тестовое сообщение">
      <div className={styles.fieldGrid}>
        <FilterSelect
          label="Канал"
          value={channel}
          options={[
            { value: "email", label: "Email" },
            { value: "telegram", label: "Telegram" },
          ]}
          onChange={(value) => {
            setChannel(value as "email" | "telegram");
            reset();
          }}
        />
        {channel === "email" ? (
          <Input
            label="Адрес получателя"
            type="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              reset();
            }}
            disabled={send.isPending}
          />
        ) : (
          <FilterSelect
            label="Получатель"
            value={telegramTarget}
            options={telegramOptions}
            onChange={(value) => {
              setTelegramTarget(value);
              reset();
            }}
          />
        )}
      </div>
      <p className={styles.note}>
        Отправляется сообщение с пометкой «тест». Оно не связано с событиями и не создаёт
        уведомлений. Не более 5 попыток за 10 минут.
      </p>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      {result ? (
        <div className={styles.testResult} role="status">
          <StatusBadge
            status={result.status === "delivered" ? "status.success" : "status.error"}
            label={result.status === "delivered" ? "Тест: отправлено" : "Тест: ошибка"}
          />
          <span>
            {result.status === "delivered"
              ? "Тестовое сообщение отправлено. Проверьте, что оно пришло получателю."
              : testSendErrorMessage(result.error_code)}
          </span>
        </div>
      ) : null}
      <div className={styles.actions}>
        <Button type="submit" variant="primary" disabled={send.isPending}>
          {send.isPending ? "Отправляем…" : "Отправить тестовое сообщение"}
        </Button>
      </div>
    </form>
  );
}
