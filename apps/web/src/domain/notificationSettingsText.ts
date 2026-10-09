import type { ConfigurationState, EncryptionState } from "../api/notificationSettings";

/** Fixed display mask for a configured secret (ADR-0048 §2.3). It is a
 * placeholder, never a value: the client never sends it. */
export const SECRET_MASK = "••••••••";

export const CONFIGURATION_LABEL: Record<ConfigurationState, string> = {
  configured: "Настроен",
  not_configured: "Не настроен",
  incomplete: "Настроен не полностью",
  invalid: "Настройки некорректны",
  secret_unavailable: "Секрет недоступен",
};

export const ENCRYPTION_MESSAGE: Record<Exclude<EncryptionState, "available">, string> = {
  missing:
    "Ключ шифрования настроек не задан на сервере. Секреты нельзя сохранить, каналы с секретами не работают. Обратитесь к администратору сервера.",
  invalid:
    "Ключ шифрования настроек на сервере задан неверно. Секреты нельзя сохранить или прочитать. Обратитесь к администратору сервера.",
};

const TEST_SEND_ERRORS: Record<string, string> = {
  channel_not_configured: "Канал не настроен.",
  channel_incomplete: "Настройки канала заполнены не полностью.",
  channel_invalid: "Настройки канала некорректны.",
  channel_secret_unavailable:
    "Не удалось прочитать сохранённый секрет: проверьте ключ шифрования на сервере или введите секрет заново.",
  destination_invalid: "Получатель указан неверно.",
  destination_not_found: "Выбранный Telegram-чат не найден.",
  destination_disabled: "Выбранный Telegram-чат отключён.",
  telegram_account_not_linked:
    "Ваш Telegram-аккаунт не привязан к TourCRM. Привяжите его или выберите группу.",
  smtp_authentication_failed: "SMTP-сервер отклонил имя пользователя или пароль.",
  smtp_connection_failed: "Не удалось подключиться к SMTP-серверу.",
  smtp_timeout: "SMTP-сервер не ответил вовремя.",
  smtp_tls_failed: "Не удалось установить защищённое соединение с SMTP-сервером.",
  smtp_rejected: "SMTP-сервер отклонил сообщение.",
  smtp_transient_failure: "SMTP-сервер временно недоступен. Попробуйте позже.",
  smtp_configuration_invalid: "SMTP-сервер не найден или не поддерживает выбранный режим.",
  telegram_configuration_invalid: "Telegram отклонил токен бота.",
  telegram_bot_blocked: "Пользователь заблокировал бота или аккаунт удалён.",
  telegram_chat_forbidden: "У бота нет доступа к этому чату.",
  telegram_chat_not_found: "Telegram-чат не найден.",
  telegram_topic_unavailable: "Тема (topic) не найдена или закрыта.",
  telegram_chat_migrated: "Группа была преобразована в супергруппу; обновите её настройки.",
  telegram_rate_limited: "Telegram временно ограничил отправку. Попробуйте позже.",
  telegram_timeout: "Telegram не ответил вовремя.",
  telegram_network_error: "Не удалось связаться с Telegram.",
  telegram_server_error: "Telegram временно недоступен.",
};

/** User-facing text for a failed test send — never a raw provider message. */
export function testSendErrorMessage(code: string | null): string {
  if (code && TEST_SEND_ERRORS[code]) return TEST_SEND_ERRORS[code];
  return "Не удалось отправить тестовое сообщение.";
}
