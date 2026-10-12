import { ApiError } from "../api/client";

/** User-facing names of the business notification catalog keys
 * (docs/04-modules/notification-event-catalog.md §3). The backend decides
 * which keys are listed and which are mandatory; an unknown key falls back
 * to the key itself. */
const EVENT_LABEL: Record<string, string> = {
  "event.created": "Новое событие для вашей группы",
  "event.updated": "Изменение места события",
  "event.cancelled": "Отмена события",
  "event.rescheduled": "Перенос события",
  "registration.created": "Ваша регистрация на событие",
  "registration.cancelled": "Отмена вашей регистрации",
  "attendance.changed": "Изменение отметки посещаемости",
  "news.published": "Публикация новости",
  "achievement.awarded": "Присвоение достижения",
};

export function eventTypeLabel(eventType: string): string {
  return EVENT_LABEL[eventType] ?? eventType;
}

function isNetworkError(error: unknown): boolean {
  return !(error instanceof ApiError);
}

const NETWORK_MESSAGE = "Не удалось связаться с сервером. Проверьте подключение и повторите.";

/** `POST /me/telegram-link/challenges` errors
 * (docs/05-api/telegram-link-api.md). */
export function linkChallengeErrorMessage(error: unknown): string {
  if (isNetworkError(error)) return NETWORK_MESSAGE;
  const apiError = error as ApiError;
  if (apiError.code === "telegram_linking_unavailable") {
    return "Привязка Telegram сейчас недоступна: бот TourCRM ещё не настроен администратором.";
  }
  if (apiError.status === 429) {
    return "Слишком много запросов ссылки. Подождите и попробуйте позже.";
  }
  if (apiError.status === 403) {
    return "Привязка Telegram для вашей учётной записи недоступна. Обновите страницу и попробуйте снова.";
  }
  return "Не удалось получить ссылку для привязки. Попробуйте ещё раз.";
}

export function unlinkErrorMessage(error: unknown): string {
  if (isNetworkError(error)) return NETWORK_MESSAGE;
  return "Не удалось отвязать Telegram. Попробуйте ещё раз.";
}

export function preferencesSaveErrorMessage(error: unknown): string {
  if (isNetworkError(error)) return NETWORK_MESSAGE;
  if ((error as ApiError).code === "invalid_notification_type") {
    return "Этот тип уведомлений нельзя изменить.";
  }
  return "Не удалось сохранить настройку. Попробуйте ещё раз.";
}

/** Local date and time, e.g. «11.10.2026, 18:45». */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
