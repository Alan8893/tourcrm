import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { usePasswordResetRequest } from "../api/auth";
import styles from "./LoginPage.module.css";

/** The `Забыли пароль?` entry point (AUTH-LOGIN-UX-SPEC.md §8): submits
 * to the existing, always-identical `POST /api/v1/auth/password-reset/
 * request` contract and shows the same confirmation regardless of
 * whether `identifier` resolves to a real account — the UI must never
 * confirm or deny account existence (spec §8/§14). */
export function PasswordResetRequestPage() {
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
    <div className={styles.page}>
      <Card className={styles.card}>
        <h1 className={styles.heading}>Восстановление пароля</h1>

        {submitted ? (
          <>
            <p className={styles.supporting} role="status">
              Если такая учётная запись существует, на связанный с ней email отправлены
              инструкции по восстановлению пароля.
            </p>
            <Link to="/login" className={styles.recoveryLink}>
              Вернуться ко входу
            </Link>
          </>
        ) : (
          <>
            <p className={styles.supporting}>
              Укажите email или логин своей учётной записи TourCRM.
            </p>
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
              <Link to="/login" className={styles.recoveryLink}>
                Вернуться ко входу
              </Link>
            </form>
          </>
        )}
      </Card>
    </div>
  );
}
