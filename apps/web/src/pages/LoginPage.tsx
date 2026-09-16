import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { BrandLogo } from "../components/ui/Icon";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { useCurrentUser, useLogin } from "../api/auth";
import { ApiError } from "../api/client";
import styles from "./LoginPage.module.css";

const DEFAULT_DESTINATION = "/";

/** AUTH-LOGIN-UX-SPEC.md §11: "Return targets must be validated as
 * internal application routes; never redirect to arbitrary external URLs
 * supplied by query parameters." Only a same-origin, root-relative path
 * (starts with exactly one `/`, never `//...` — protocol-relative to a
 * different host — and never an absolute URL) is accepted; anything else
 * falls back to the default destination. */
function safeReturnTarget(value: string | null): string {
  if (!value) return DEFAULT_DESTINATION;
  if (!value.startsWith("/") || value.startsWith("//")) return DEFAULT_DESTINATION;
  try {
    // A relative path parses against any base; if the result's origin
    // differs, `value` smuggled a scheme/host past the leading-slash
    // check (e.g. "/\\evil.example").
    const resolved = new URL(value, window.location.origin);
    if (resolved.origin !== window.location.origin) return DEFAULT_DESTINATION;
  } catch {
    return DEFAULT_DESTINATION;
  }
  return value;
}

/** `Вход в TourCRM` (Issue #100 / TH-0090, AUTH-LOGIN-UX-SPEC.md). Public
 * route, deliberately rendered outside `AppShell` (spec §4: "Do not place
 * unrelated navigation items, dashboard content, or role-selection
 * controls on the login screen"). */
export function LoginPage() {
  const { data: me, isPending: mePending } = useCurrentUser();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const login = useLogin();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [formError, setFormError] = useState<"credentials" | "network" | null>(null);
  const errorId = useId();

  const destination = safeReturnTarget(searchParams.get("next"));

  // spec §11: an already-authenticated visitor never sees the login form —
  // including the brief window before the first `/auth/me` resolution.
  if (mePending) {
    return (
      <div className={styles.page}>
        <Loading label="Загрузка…" />
      </div>
    );
  }
  if (me) {
    return <Navigate to={destination} replace />;
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (login.isPending) return; // duplicate-submit prevention
    setFormError(null);
    login.mutate(
      { identifier, password },
      {
        onSuccess: () => {
          navigate(destination, { replace: true });
        },
        onError: (error) => {
          if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
            setFormError("credentials");
          } else {
            setFormError("network");
          }
        },
      },
    );
  }

  return (
    <div className={styles.page}>
      <Card className={styles.card}>
        <div className={styles.brand}>
          <BrandLogo height={40} />
        </div>
        <h1 className={styles.heading}>Вход в TourCRM</h1>
        <p className={styles.supporting}>Войдите с помощью своей учётной записи TourCRM.</p>

        <form className={styles.form} onSubmit={handleSubmit} noValidate>
          <Input
            label="Email или логин"
            type="text"
            name="identifier"
            autoComplete="username"
            value={identifier}
            onChange={(event) => setIdentifier(event.target.value)}
            aria-describedby={formError ? errorId : undefined}
            required
          />
          <Input
            label="Пароль"
            type={passwordVisible ? "text" : "password"}
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-describedby={formError ? errorId : undefined}
            required
          />
          <button
            type="button"
            className={styles.toggleVisibility}
            onClick={() => setPasswordVisible((value) => !value)}
            aria-pressed={passwordVisible}
          >
            {passwordVisible ? "Скрыть пароль" : "Показать пароль"}
          </button>

          {formError ? (
            <p className={styles.error} role="alert" id={errorId}>
              {formError === "credentials"
                ? "Не удалось войти. Проверьте логин и пароль."
                : "Не удалось подключиться к серверу. Попробуйте ещё раз."}
            </p>
          ) : null}

          <Button type="submit" variant="primary" disabled={login.isPending}>
            {login.isPending ? "Вход…" : "Войти"}
          </Button>

          <Link to="/password-reset" className={styles.recoveryLink}>
            Забыли пароль?
          </Link>
        </form>
      </Card>
    </div>
  );
}
