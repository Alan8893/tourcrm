import { useId, useState } from "react";

import { Button } from "../components/ui/Button";
import { Loading } from "../components/ui/Loading";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import {
  useNotificationPreferences,
  useUpdateNotificationPreferences,
  type NotificationPreferencesUpdate,
} from "../api/personalNotifications";
import { eventTypeLabel, preferencesSaveErrorMessage } from "../domain/personalNotificationText";
import styles from "./SettingsPage.module.css";

/**
 * The user's own personal Telegram notification preferences (Issue #343)
 * over `GET`/`PUT /me/notification-preferences`
 * (docs/05-api/notification-preferences-api.md).
 *
 * Every change is a partial update of exactly one value: the master switch
 * request never names an event type and an event request never names the
 * master switch, so toggling the master switch cannot change — or turn on —
 * any per-event choice. Each switch shows the stored value returned by the
 * backend, which also enforces these values when it plans a delivery;
 * mandatory types are shown read-only, never as switches.
 */
export function SettingsNotificationPreferences() {
  const preferences = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const notify = useNotify();
  const [failed, setFailed] = useState<NotificationPreferencesUpdate | null>(null);
  const masterHintId = useId();
  const mandatoryHintId = useId();

  if (preferences.isLoading) return <Loading label="Загружаем настройки уведомлений…" />;
  if (preferences.isError || !preferences.data) {
    return (
      <div className={styles.loadError}>
        <p className={styles.error} role="alert">
          Не удалось загрузить настройки уведомлений.
        </p>
        <Button variant="secondary" onClick={() => void preferences.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }

  function save(payload: NotificationPreferencesUpdate) {
    setFailed(null);
    update.mutate(payload, {
      onSuccess: () => notify("success", "Настройка сохранена"),
      onError: () => setFailed(payload),
    });
  }

  const current = preferences.data;
  const optional = current.events.filter((event) => !event.mandatory);
  const mandatory = current.events.filter((event) => event.mandatory);

  return (
    <div className={styles.form}>
      {!current.telegram_linked ? (
        <p className={styles.warning}>
          Telegram не привязан: личные сообщения не будут доставляться, пока вы не привяжете
          аккаунт.
        </p>
      ) : null}

      <div className={styles.preferenceGroup}>
        <label className={styles.switch}>
          <input
            type="checkbox"
            role="switch"
            checked={current.personal_enabled}
            disabled={update.isPending}
            aria-describedby={masterHintId}
            onChange={() => save({ personal_enabled: !current.personal_enabled })}
          />
          <span className={styles.switchLabel}>Личные сообщения в Telegram</span>
        </label>
        <p className={styles.note} id={masterHintId}>
          Общий переключатель для необязательных уведомлений. При выключении ваш выбор ниже
          сохраняется, а при включении не меняется: включаются только отмеченные типы.
        </p>
      </div>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Необязательные уведомления</legend>
        {!current.personal_enabled ? (
          <p className={styles.note}>
            Сейчас личные сообщения выключены, поэтому эти уведомления не отправляются. Выбор можно
            изменить заранее — он начнёт действовать после включения.
          </p>
        ) : null}
        {optional.length === 0 ? (
          <p className={styles.note}>Нет уведомлений, которые можно настроить.</p>
        ) : (
          <ul className={styles.preferenceList}>
            {optional.map((event) => (
              <li key={event.event_type} className={styles.preferenceRow}>
                <label className={styles.switch}>
                  <input
                    type="checkbox"
                    role="switch"
                    checked={event.enabled}
                    disabled={update.isPending}
                    onChange={() => save({ events: { [event.event_type]: !event.enabled } })}
                  />
                  <span>{eventTypeLabel(event.event_type)}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      {mandatory.length > 0 ? (
        <section className={styles.preferenceGroup} aria-labelledby={mandatoryHintId}>
          <h4 className={styles.legend} id={mandatoryHintId}>
            Обязательные уведомления
          </h4>
          <ul className={styles.preferenceList}>
            {mandatory.map((event) => (
              <li key={event.event_type} className={styles.preferenceRow}>
                <span>{eventTypeLabel(event.event_type)}</span>
                <StatusBadge status="status.info" label="Обязательное" />
              </li>
            ))}
          </ul>
          <p className={styles.note}>
            Об отмене и переносе события, на которое вы зарегистрированы, мы сообщаем даже при
            выключенных личных сообщениях: от этих уведомлений нельзя отказаться в личных
            настройках. Их доставку может остановить только администратор — выключив Telegram для
            всей системы или отключив правило уведомления.
          </p>
        </section>
      ) : null}

      <p className={styles.note}>
        Эти настройки касаются только личных сообщений вам в Telegram. Публикации в группе и темах
        клуба в Telegram настраивает администратор — на них ваши настройки не влияют.
      </p>

      {failed && update.isError ? (
        <div className={styles.loadError}>
          <p className={styles.error} role="alert">
            {preferencesSaveErrorMessage(update.error)}
          </p>
          <Button variant="secondary" onClick={() => save(failed)} disabled={update.isPending}>
            Повторить
          </Button>
        </div>
      ) : null}
    </div>
  );
}
