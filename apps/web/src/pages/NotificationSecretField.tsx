import { useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Input } from "../components/ui/Input";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import { ApiError } from "../api/client";
import { useClearSecret, useReplaceSecret, type SecretName } from "../api/notificationSettings";
import { SECRET_MASK } from "../domain/notificationSettingsText";
import styles from "./NotificationSettingsPage.module.css";

function secretErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  if (error.code === "settings_encryption_unavailable") {
    return "Секрет не сохранён: на сервере не настроен ключ шифрования.";
  }
  if (error.code === "invalid_secret" || error.status === 422) {
    return "Значение не подходит. Проверьте его и введите заново.";
  }
  if (error.status === 403) return "Недостаточно прав для изменения секрета.";
  return "Не удалось сохранить изменения. Попробуйте ещё раз.";
}

export type NotificationSecretFieldProps = {
  name: SecretName;
  label: string;
  configured: boolean;
  /** Hint under the new-value input. */
  inputHint?: string;
  disabled?: boolean;
};

/**
 * A write-only secret (ADR-0048 §2.3). Shows only the fixed mask and the
 * configured state — the stored value is never fetched. Three explicit
 * outcomes:
 *
 * - unchanged (default): nothing is sent, also when other settings are
 *   saved;
 * - «Заменить» / «Задать»: opens an empty input; the new value is sent once
 *   with its own request and cleared from local state and the mutation
 *   cache as soon as the request settles;
 * - «Очистить»: a separate action behind a confirmation dialog.
 */
export function NotificationSecretField({
  name,
  label,
  configured,
  inputHint,
  disabled = false,
}: NotificationSecretFieldProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const replace = useReplaceSecret(name);
  const clear = useClearSecret(name);
  const notify = useNotify();
  const pending = replace.isPending || clear.isPending;

  function cancel() {
    setEditing(false);
    setValue("");
    setError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!value.trim() || pending) return;
    setError(null);
    replace.mutate(value, {
      onSuccess: () => {
        cancel();
        notify("success", configured ? `${label}: значение заменено` : `${label}: значение сохранено`);
      },
      onError: (mutationError) => setError(secretErrorMessage(mutationError)),
      // Never keep the secret in the mutation cache.
      onSettled: () => replace.reset(),
    });
  }

  function handleClear() {
    clear.mutate(undefined, {
      onSuccess: () => {
        setConfirmClear(false);
        notify("success", `${label}: значение очищено`);
      },
      onError: (mutationError) => {
        setConfirmClear(false);
        setError(secretErrorMessage(mutationError));
      },
    });
  }

  return (
    <div className={styles.secret} data-testid={`secret-${name}`}>
      <div className={styles.secretHeader}>
        <span className={styles.secretLabel}>{label}</span>
        <StatusBadge
          status={configured ? "status.success" : "status.warning"}
          label={configured ? "Настроен" : "Не настроен"}
        />
      </div>
      {!editing ? (
        <div className={styles.secretRow}>
          <span className={styles.mask} aria-label={configured ? `${label}: скрыт` : undefined}>
            {configured ? SECRET_MASK : "—"}
          </span>
          <div className={styles.actions}>
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setEditing(true);
                setError(null);
              }}
              disabled={disabled || pending}
            >
              {configured ? "Заменить" : "Задать"}
            </Button>
            {configured ? (
              <Button
                type="button"
                variant="destructive"
                onClick={() => setConfirmClear(true)}
                disabled={disabled || pending}
              >
                Очистить
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <form className={styles.secretForm} onSubmit={handleSubmit} noValidate>
          <Input
            label={configured ? `Новое значение: ${label}` : label}
            type="password"
            autoComplete="new-password"
            value={value}
            hint={inputHint}
            onChange={(event) => {
              setValue(event.target.value);
              setError(null);
            }}
            disabled={pending}
          />
          <div className={styles.actions}>
            <Button type="submit" variant="primary" disabled={!value.trim() || pending}>
              {replace.isPending ? "Сохранение…" : "Сохранить"}
            </Button>
            <Button type="button" variant="secondary" onClick={cancel} disabled={pending}>
              Отмена
            </Button>
          </div>
        </form>
      )}
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <ConfirmDialog
        open={confirmClear}
        title={`Очистить: ${label}?`}
        description="Сохранённое значение будет удалено без возможности восстановления. Канал перестанет работать, пока не будет задано новое значение."
        confirmLabel="Очистить"
        destructive
        pending={clear.isPending}
        onConfirm={handleClear}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}
