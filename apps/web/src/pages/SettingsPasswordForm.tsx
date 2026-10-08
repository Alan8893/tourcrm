import { useId, useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { useNotify } from "../components/ui/notificationContext";
import { ApiError } from "../api/client";
import { useChangePassword } from "../api/auth";
import styles from "./SettingsPage.module.css";

/** User-facing text for a failed `POST /auth/password/change` — never the
 * raw backend message. */
function passwordChangeErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  if (error.code === "incorrect_current_password") return "Текущий пароль указан неверно.";
  if (error.code === "weak_password") {
    return "Новый пароль слишком простой. Придумайте более длинный пароль.";
  }
  if (error.status === 429) return "Слишком много попыток. Попробуйте позже.";
  return "Не удалось изменить пароль. Попробуйте ещё раз.";
}

/**
 * TH #311: «Изменить пароль» through the existing self-service
 * `POST /auth/password/change`. The confirmation is checked here only to
 * catch typos (no request on mismatch); the current password and the
 * password policy are the backend's. Passwords live only in this form's
 * local state for the request lifecycle — cleared on success, never
 * persisted, logged or kept in the mutation cache — and the session is
 * left as the backend leaves it (no logout here).
 */
export function SettingsPasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [succeeded, setSucceeded] = useState(false);
  const change = useChangePassword();
  const notify = useNotify();
  const messageId = useId();

  const complete = Boolean(currentPassword && newPassword && confirmPassword);

  function clearMessages() {
    setError(null);
    setSucceeded(false);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (change.isPending || !complete) return;
    clearMessages();
    if (newPassword !== confirmPassword) {
      setError("Новый пароль и подтверждение не совпадают.");
      return;
    }
    change.mutate(
      { current_password: currentPassword, new_password: newPassword },
      {
        onSuccess: () => {
          setCurrentPassword("");
          setNewPassword("");
          setConfirmPassword("");
          setSucceeded(true);
          notify("success", "Пароль изменён");
        },
        onError: (mutationError) => setError(passwordChangeErrorMessage(mutationError)),
        // Drop the request (and its password variables) from the
        // mutation cache as soon as it settles.
        onSettled: () => change.reset(),
      },
    );
  }

  const describedBy = error || succeeded ? messageId : undefined;

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate aria-label="Изменить пароль">
      <div className={styles.passwordFields}>
        <Input
          label="Текущий пароль"
          type="password"
          name="current_password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(event) => {
            setCurrentPassword(event.target.value);
            clearMessages();
          }}
          disabled={change.isPending}
          aria-describedby={describedBy}
          required
        />
        <Input
          label="Новый пароль"
          type="password"
          name="new_password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(event) => {
            setNewPassword(event.target.value);
            clearMessages();
          }}
          disabled={change.isPending}
          aria-describedby={describedBy}
          required
        />
        <Input
          label="Подтверждение нового пароля"
          type="password"
          name="confirm_password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(event) => {
            setConfirmPassword(event.target.value);
            clearMessages();
          }}
          disabled={change.isPending}
          aria-describedby={describedBy}
          required
        />
      </div>

      {error ? (
        <p className={styles.error} role="alert" id={messageId}>
          {error}
        </p>
      ) : null}
      {succeeded ? (
        <p className={styles.success} role="status" id={messageId}>
          Пароль изменён. Используйте новый пароль при следующем входе.
        </p>
      ) : null}

      <div className={styles.actions}>
        <Button type="submit" variant="primary" disabled={!complete || change.isPending}>
          {change.isPending ? "Сохранение…" : "Изменить пароль"}
        </Button>
      </div>
    </form>
  );
}
