import { useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { FilterSelect } from "../components/ui/FilterSelect";
import { Input } from "../components/ui/Input";
import { useNotify } from "../components/ui/notificationContext";
import { ApiError } from "../api/client";
import {
  useUpdateEmailSettings,
  useUpdateTelegramSettings,
  type EmailSettings,
  type SmtpSecurity,
  type TelegramSettings,
} from "../api/notificationSettings";
import { NotificationSecretField } from "./NotificationSecretField";
import styles from "./NotificationSettingsPage.module.css";

const SECURITY_OPTIONS: { value: SmtpSecurity; label: string }[] = [
  { value: "starttls", label: "STARTTLS (обычно порт 587)" },
  { value: "ssl", label: "SSL/TLS (обычно порт 465)" },
  { value: "none", label: "Без шифрования (только локальная сеть)" },
];

const FIELD_ERRORS: Record<string, string> = {
  invalid_smtp_host: "Укажите корректное имя SMTP-сервера (без протокола и пробелов).",
  invalid_sender_email: "Укажите корректный адрес отправителя.",
  invalid_sender_name: "Имя отправителя не должно содержать переносов строк.",
  invalid_smtp_username: "Имя пользователя SMTP не должно содержать переносов строк.",
  invalid_bot_username: "Укажите username бота, например club_bot (должен заканчиваться на bot).",
};

function settingsErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  if (FIELD_ERRORS[error.code]) return FIELD_ERRORS[error.code];
  if (error.status === 422) return "Проверьте заполнение полей.";
  if (error.status === 403) return "Недостаточно прав для изменения настроек.";
  return "Не удалось сохранить настройки. Попробуйте ещё раз.";
}

function nullable(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/** Non-secret SMTP settings. Saving them never sends the password — it is
 * a separate write-only field below. */
export function EmailSettingsForm({
  settings,
  secretsWritable,
}: {
  settings: EmailSettings;
  secretsWritable: boolean;
}) {
  const [host, setHost] = useState(settings.smtp_host ?? "");
  const [port, setPort] = useState(settings.smtp_port ? String(settings.smtp_port) : "");
  const [security, setSecurity] = useState<SmtpSecurity>(settings.smtp_security);
  const [username, setUsername] = useState(settings.smtp_username ?? "");
  const [senderEmail, setSenderEmail] = useState(settings.sender_email ?? "");
  const [senderName, setSenderName] = useState(settings.sender_name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const update = useUpdateEmailSettings();
  const notify = useNotify();

  function touched() {
    setError(null);
    setSaved(false);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (update.isPending) return;
    touched();
    const portNumber = port.trim() ? Number(port.trim()) : null;
    if (portNumber !== null && (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535)) {
      setError("Порт должен быть числом от 1 до 65535.");
      return;
    }
    update.mutate(
      {
        smtp_host: nullable(host),
        smtp_port: portNumber,
        smtp_security: security,
        smtp_username: nullable(username),
        sender_email: nullable(senderEmail),
        sender_name: nullable(senderName),
      },
      {
        onSuccess: () => {
          setSaved(true);
          notify("success", "Настройки Email сохранены");
        },
        onError: (mutationError) => setError(settingsErrorMessage(mutationError)),
      },
    );
  }

  return (
    <div className={styles.integration}>
      <form className={styles.form} onSubmit={handleSubmit} noValidate aria-label="Настройки Email">
        <div className={styles.fieldGrid}>
          <Input
            label="SMTP-сервер"
            value={host}
            placeholder="smtp.example.org"
            onChange={(event) => {
              setHost(event.target.value);
              touched();
            }}
            disabled={update.isPending}
          />
          <Input
            label="Порт"
            inputMode="numeric"
            value={port}
            hint="Пусто — порт по умолчанию для выбранного режима"
            onChange={(event) => {
              setPort(event.target.value);
              touched();
            }}
            disabled={update.isPending}
          />
          <FilterSelect
            label="Шифрование соединения"
            value={security}
            options={SECURITY_OPTIONS}
            onChange={(value) => {
              setSecurity(value as SmtpSecurity);
              touched();
            }}
          />
          <Input
            label="Имя пользователя SMTP"
            value={username}
            autoComplete="off"
            hint="Пусто — без авторизации"
            onChange={(event) => {
              setUsername(event.target.value);
              touched();
            }}
            disabled={update.isPending}
          />
          <Input
            label="Адрес отправителя"
            type="email"
            value={senderEmail}
            placeholder="noreply@example.org"
            onChange={(event) => {
              setSenderEmail(event.target.value);
              touched();
            }}
            disabled={update.isPending}
          />
          <Input
            label="Имя отправителя"
            value={senderName}
            onChange={(event) => {
              setSenderName(event.target.value);
              touched();
            }}
            disabled={update.isPending}
          />
        </div>
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        {saved ? (
          <p className={styles.success} role="status">
            Настройки Email сохранены.
          </p>
        ) : null}
        <div className={styles.actions}>
          <Button type="submit" variant="primary" disabled={update.isPending}>
            {update.isPending ? "Сохранение…" : "Сохранить настройки Email"}
          </Button>
        </div>
      </form>
      <NotificationSecretField
        name="smtp_password"
        label="Пароль SMTP"
        configured={settings.password_configured}
        disabled={!secretsWritable}
      />
    </div>
  );
}

/** Bot username (public) and the write-only bot token. */
export function TelegramSettingsForm({
  settings,
  secretsWritable,
}: {
  settings: TelegramSettings;
  secretsWritable: boolean;
}) {
  const [username, setUsername] = useState(settings.bot_username ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const update = useUpdateTelegramSettings();
  const notify = useNotify();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (update.isPending) return;
    setError(null);
    setSaved(false);
    update.mutate(
      { bot_username: nullable(username) },
      {
        onSuccess: () => {
          setSaved(true);
          notify("success", "Настройки Telegram сохранены");
        },
        onError: (mutationError) => setError(settingsErrorMessage(mutationError)),
      },
    );
  }

  return (
    <div className={styles.integration}>
      <form className={styles.form} onSubmit={handleSubmit} noValidate aria-label="Настройки Telegram">
        <Input
          label="Username бота"
          value={username}
          placeholder="club_bot"
          hint="Нужен для ссылок привязки аккаунтов Telegram"
          onChange={(event) => {
            setUsername(event.target.value);
            setError(null);
            setSaved(false);
          }}
          disabled={update.isPending}
        />
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
        {saved ? (
          <p className={styles.success} role="status">
            Настройки Telegram сохранены.
          </p>
        ) : null}
        <div className={styles.actions}>
          <Button type="submit" variant="primary" disabled={update.isPending}>
            {update.isPending ? "Сохранение…" : "Сохранить настройки Telegram"}
          </Button>
        </div>
      </form>
      <NotificationSecretField
        name="telegram_bot_token"
        label="Токен бота"
        configured={settings.bot_token_configured}
        inputHint="Токен в том виде, в котором его выдал @BotFather"
        disabled={!secretsWritable}
      />
    </div>
  );
}
