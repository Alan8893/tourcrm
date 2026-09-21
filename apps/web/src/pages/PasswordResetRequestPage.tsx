import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { usePasswordResetConfirm, usePasswordResetRequest } from "../api/auth";
import { ApiError } from "../api/client";
import styles from "./LoginPage.module.css";

type Mode = "request" | "confirm";

/** The `Забыли пароль?` entry point (AUTH-LOGIN-UX-SPEC.md §8) — and,
 * per ADR-0038 §2/§7 (TH-0113), the SAME canonical page for both
 * self-service password recovery and completing an administrator-issued
 * first-access/reset one-time code: the underlying mechanism
 * (`POST /auth/password-reset/confirm`) is identical either way, so no
 * separate `/first-access` or `/set-password` route exists. Only the
 * heading text differs ("Установите пароль" is not shown here — the code
 * itself carries no signal distinguishing "first access" from "reset",
 * so this page uses one neutral label, "Ввести код и задать пароль", for
 * both cases, matching the task's own "the underlying mechanism is one"
 * framing without inventing a distinction the backend does not make).
 */
export function PasswordResetRequestPage() {
  const [mode, setMode] = useState<Mode>("request");

  return (
    <div className={styles.page}>
      <Card className={styles.card}>
        {mode === "request" ? (
          <RequestStep onSwitchToConfirm={() => setMode("confirm")} />
        ) : (
          <ConfirmStep onSwitchToRequest={() => setMode("request")} />
        )}
      </Card>
    </div>
  );
}

function RequestStep({ onSwitchToConfirm }: { onSwitchToConfirm: () => void }) {
  const [identifier, setIdentifier] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const request = usePasswordResetRequest();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.isPending) return;
    request.mutate(
      { identifier },
      {
        // Always the same outcome shown to the user, success or failure —
        // the backend itself never distinguishes, and neither may this UI.
        onSettled: () => setSubmitted(true),
      },
    );
  }

  return (
    <>
      <h1 className={styles.heading}>Восстановление пароля</h1>

      {submitted ? (
        <>
          <p className={styles.supporting} role="status">
            Если такая учётная запись существует, на связанный с ней email отправлены инструкции
            по восстановлению пароля.
          </p>
          <Link to="/login" className={styles.recoveryLink}>
            Вернуться ко входу
          </Link>
        </>
      ) : (
        <>
          <p className={styles.supporting}>Укажите email или логин своей учётной записи TourCRM.</p>
          <form className={styles.form} onSubmit={handleSubmit} noValidate>
            <Input
              label="Email или логин"
              type="text"
              name="identifier"
              autoComplete="username"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
              required
            />
            <Button type="submit" variant="primary" disabled={request.isPending}>
              {request.isPending ? "Отправка…" : "Отправить"}
            </Button>
            <button type="button" className={styles.toggleVisibility} onClick={onSwitchToConfirm}>
              У меня уже есть одноразовый код
            </button>
            <Link to="/login" className={styles.recoveryLink}>
              Вернуться ко входу
            </Link>
          </form>
        </>
      )}
    </>
  );
}

/** Step 2 of the one canonical flow: the user has a one-time code — from
 * a self-service reset email/link (once such delivery exists) or from an
 * administrator (ADR-0038 §3's MVP fallback) — and enters it here
 * together with their new password. The code is always typed/pasted
 * into this field; it is never read from a URL query parameter, so a
 * temporary credential an admin hands over out of band never ends up in
 * browser history or server access logs. */
function ConfirmStep({ onSwitchToRequest }: { onSwitchToRequest: () => void }) {
  const [token, setToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [mismatchError, setMismatchError] = useState(false);
  const [succeeded, setSucceeded] = useState(false);
  const confirm = usePasswordResetConfirm();
  const errorId = useId();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (confirm.isPending) return;
    setMismatchError(false);
    if (newPassword !== confirmPassword) {
      setMismatchError(true);
      return;
    }
    confirm.mutate(
      { token, new_password: newPassword },
      { onSuccess: () => setSucceeded(true) },
    );
  }

  if (succeeded) {
    return (
      <>
        <h1 className={styles.heading}>Пароль установлен</h1>
        <p className={styles.supporting} role="status">
          Ваш новый пароль сохранён. Теперь вы можете войти с его помощью.
        </p>
        <Link to="/login" className={styles.recoveryLink}>
          Перейти ко входу
        </Link>
      </>
    );
  }

  const hasError = mismatchError || confirm.isError;
  const errorMessage = mismatchError
    ? "Пароли не совпадают."
    : confirm.error instanceof ApiError
      ? confirm.error.message
      : null;

  return (
    <>
      <h1 className={styles.heading}>Ввести код и задать пароль</h1>
      <p className={styles.supporting}>
        Введите одноразовый код доступа и задайте новый пароль для своей учётной записи TourCRM.
      </p>
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <Input
          label="Одноразовый код"
          type="text"
          name="token"
          autoComplete="one-time-code"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          aria-describedby={hasError ? errorId : undefined}
          required
        />
        <Input
          label="Новый пароль"
          type="password"
          name="new_password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(event) => setNewPassword(event.target.value)}
          aria-describedby={hasError ? errorId : undefined}
          required
        />
        <Input
          label="Повторите пароль"
          type="password"
          name="confirm_password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          aria-describedby={hasError ? errorId : undefined}
          required
        />

        {hasError && errorMessage ? (
          <p className={styles.error} role="alert" id={errorId}>
            {errorMessage}
          </p>
        ) : null}

        <Button type="submit" variant="primary" disabled={confirm.isPending}>
          {confirm.isPending ? "Сохранение…" : "Установить пароль"}
        </Button>
        <button type="button" className={styles.toggleVisibility} onClick={onSwitchToRequest}>
          У меня нет кода — запросить сброс пароля
        </button>
        <Link to="/login" className={styles.recoveryLink}>
          Вернуться ко входу
        </Link>
      </form>
    </>
  );
}
