import { useEffect, useRef, useState } from "react";

import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import buttonStyles from "../components/ui/Button.module.css";
import {
  useCreateTelegramLinkChallenge,
  useInvalidatePersonalTelegram,
  useTelegramLinkStatus,
  useUnlinkTelegram,
  type TelegramLinkChallenge,
} from "../api/personalNotifications";
import {
  formatDateTime,
  linkChallengeErrorMessage,
  unlinkErrorMessage,
} from "../domain/personalNotificationText";
import styles from "./SettingsPage.module.css";

/**
 * The user's own Telegram link (Issue #343) over the existing self-scoped
 * API (docs/05-api/telegram-link-api.md). The browser never sends or sees
 * a Telegram id: the link is established only when the bot receives
 * `/start <token>` from the user's own Telegram account.
 *
 * The one-time deep link lives only in component state, is used only as
 * the `href` of «Открыть Telegram» (the token is never rendered as text)
 * and is dropped once it expires, the link completes or the user unlinks.
 */
export function SettingsTelegramLink() {
  const notify = useNotify();
  const [challenge, setChallenge] = useState<TelegramLinkChallenge | null>(null);
  const [expired, setExpired] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const status = useTelegramLinkStatus({ pollWhilePending: challenge !== null && !expired });
  const issue = useCreateTelegramLinkChallenge();
  const unlink = useUnlinkTelegram();
  const invalidate = useInvalidatePersonalTelegram();

  // The backend's expiry is authoritative; hide the link once it passes.
  useEffect(() => {
    if (!challenge) return;
    const remaining = new Date(challenge.expires_at).getTime() - Date.now();
    if (remaining <= 0) {
      setExpired(true);
      return;
    }
    const timer = window.setTimeout(() => setExpired(true), remaining);
    return () => window.clearTimeout(timer);
  }, [challenge]);

  // The bot completed the link (seen by polling, «Проверить привязку» or a
  // refetch on focus): drop the used link and refresh the preferences,
  // whose `telegram_linked` follows. Once per unlinked → linked change.
  const previousLinked = useRef<boolean | undefined>(undefined);
  useEffect(() => {
    const previous = previousLinked.current;
    previousLinked.current = status.data?.linked;
    if (previous === false && status.data?.linked === true) {
      setChallenge(null);
      setExpired(false);
      notify("success", "Telegram привязан");
      invalidate();
    }
  }, [status.data?.linked, notify, invalidate]);

  function requestLink() {
    issue.mutate(undefined, {
      onSuccess: (issued) => {
        setExpired(false);
        setChallenge(issued);
      },
      onError: () => setChallenge(null),
    });
  }

  async function checkLink() {
    const result = await status.refetch();
    if (result.data && !result.data.linked) {
      notify("info", "Привязка ещё не завершена. Откройте ссылку и нажмите «Start» в Telegram.");
    }
  }

  function handleUnlink() {
    unlink.mutate(undefined, {
      onSuccess: () => {
        setConfirmUnlink(false);
        setChallenge(null);
        notify("success", "Telegram отвязан");
      },
      onError: (error) => {
        setConfirmUnlink(false);
        notify("error", unlinkErrorMessage(error));
      },
    });
  }

  if (status.isLoading) return <Loading label="Загружаем статус Telegram…" />;
  if (status.isError || !status.data) {
    return (
      <div className={styles.loadError}>
        <p className={styles.error} role="alert">
          Не удалось загрузить статус привязки Telegram.
        </p>
        <Button variant="secondary" onClick={() => void status.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }

  if (status.data.linked) {
    return (
      <div className={styles.form}>
        <div className={styles.statusRow}>
          <StatusBadge status="status.success" label="Telegram привязан" />
          {status.data.linked_at ? (
            <span className={styles.note}>с {formatDateTime(status.data.linked_at)}</span>
          ) : null}
        </div>
        <p className={styles.note}>Личные сообщения TourCRM приходят в ваш аккаунт Telegram.</p>
        <div className={styles.actions}>
          <Button
            variant="destructive"
            onClick={() => setConfirmUnlink(true)}
            disabled={unlink.isPending}
          >
            Отвязать Telegram
          </Button>
        </div>
        <ConfirmDialog
          open={confirmUnlink}
          title="Отвязать Telegram?"
          description="Личные сообщения TourCRM, включая сообщения об отмене и переносе событий, перестанут приходить в Telegram. Привязать аккаунт снова можно в любой момент."
          confirmLabel="Отвязать"
          destructive
          pending={unlink.isPending}
          onConfirm={handleUnlink}
          onCancel={() => setConfirmUnlink(false)}
        />
      </div>
    );
  }

  const active = challenge !== null && !expired;
  return (
    <div className={styles.form}>
      <div className={styles.statusRow}>
        <StatusBadge status="status.ended" label="Telegram не привязан" />
      </div>
      {active ? (
        <>
          <ol className={styles.steps}>
            <li>Нажмите «Открыть Telegram» — откроется чат с ботом TourCRM.</li>
            <li>В Telegram нажмите кнопку «Start» («Запустить»).</li>
            <li>Вернитесь сюда: статус обновится автоматически.</li>
          </ol>
          <p className={styles.note}>
            Ссылка одноразовая и действует до {formatDateTime(challenge.expires_at)}. Не
            пересылайте её другим людям.
          </p>
          <div className={styles.actions}>
            <a
              className={`${buttonStyles.button} ${buttonStyles.primary} ${styles.linkButton}`}
              href={challenge.deep_link}
              target="_blank"
              rel="noopener noreferrer"
            >
              Открыть Telegram
            </a>
            <Button
              variant="secondary"
              onClick={() => void checkLink()}
              disabled={status.isFetching}
            >
              Проверить привязку
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className={styles.note}>
            Привяжите свой аккаунт Telegram, чтобы получать личные уведомления TourCRM. Мы
            сформируем одноразовую ссылку на бота клуба; номер или ID Telegram вводить не нужно.
          </p>
          {expired ? (
            <p className={styles.error} role="alert">
              Срок действия ссылки истёк. Получите новую ссылку.
            </p>
          ) : null}
          {issue.isError ? (
            <p className={styles.error} role="alert">
              {linkChallengeErrorMessage(issue.error)}
            </p>
          ) : null}
          <div className={styles.actions}>
            <Button variant="primary" onClick={requestLink} disabled={issue.isPending}>
              {expired || issue.isError ? "Получить новую ссылку" : "Привязать Telegram"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
