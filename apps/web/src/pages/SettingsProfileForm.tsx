import { useId, useState, type FormEvent } from "react";

import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { useNotify } from "../components/ui/notificationContext";
import { ApiError } from "../api/client";
import { useUpdatePerson, type Person, type PersonFields } from "../api/people";
import { formatCalendarDate } from "../domain/inventoryFormat";
import styles from "./SettingsPage.module.css";

/** `PersonUpdateRequest` limits (app/api/v1/persons_schemas.py) — mirrored
 * only to give an early, friendly message; the backend stays the
 * authority and its own 422 is still mapped below. */
const NAME_MAX_LENGTH = 255;
const PHONE_MAX_LENGTH = 32;

type Values = {
  first_name: string;
  last_name: string;
  middle_name: string;
  phone: string;
  address: string;
};

const FIELD_LABELS: Record<string, string> = {
  first_name: "Имя",
  last_name: "Фамилия",
  middle_name: "Отчество",
  phone: "Телефон",
  address: "Адрес",
};

function valuesFromPerson(person: Person): Values {
  return {
    first_name: person.first_name,
    last_name: person.last_name,
    middle_name: person.middle_name ?? "",
    phone: person.phone ?? "",
    address: person.address ?? "",
  };
}

/** PATCH semantics: only the fields that actually changed, with an
 * emptied optional field sent as `null` (same rules as the People
 * EditPersonDialog). */
function changedFields(person: Person, values: Values): PersonFields {
  const fields: PersonFields = {};
  if (values.first_name.trim() !== person.first_name) fields.first_name = values.first_name.trim();
  if (values.last_name.trim() !== person.last_name) fields.last_name = values.last_name.trim();
  const optional = ["middle_name", "phone", "address"] as const;
  for (const key of optional) {
    const next = values[key].trim() || null;
    if (next !== person[key]) fields[key] = next;
  }
  return fields;
}

function validate(values: Values): string | null {
  if (!values.last_name.trim()) return "Укажите фамилию.";
  if (!values.first_name.trim()) return "Укажите имя.";
  for (const key of ["first_name", "last_name", "middle_name"] as const) {
    if (values[key].trim().length > NAME_MAX_LENGTH) {
      return `Поле «${FIELD_LABELS[key]}» не может быть длиннее ${NAME_MAX_LENGTH} символов.`;
    }
  }
  if (values.phone.trim().length > PHONE_MAX_LENGTH) {
    return `Телефон не может быть длиннее ${PHONE_MAX_LENGTH} символов.`;
  }
  return null;
}

/** User-facing text for a failed PATCH — never the raw backend message
 * or request internals. */
function profileSaveErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  if (error.status === 422) {
    const fields = (error.details as { fields?: { field?: string }[] } | undefined)?.fields ?? [];
    const labels = [
      ...new Set(fields.map((item) => FIELD_LABELS[item.field ?? ""]).filter(Boolean)),
    ];
    return labels.length > 0
      ? `Проверьте поля: ${labels.join(", ")}.`
      : "Проверьте введённые данные.";
  }
  if (error.status === 403 || error.status === 404) {
    return "У вас нет прав на изменение этих данных.";
  }
  return "Не удалось сохранить изменения. Попробуйте ещё раз.";
}

/**
 * TH #311: the signed-in user's own Person data, edited through the
 * canonical `PATCH /persons/{person_id}` (`person.update(self)`, matrix
 * §4.1). Only the self-updatable fields are inputs; `email` and
 * `birth_date` are shown read-only because `self` may not change them.
 * The backend stays authoritative — a refused field still surfaces as an
 * error here.
 */
export function SettingsProfileForm({ person }: { person: Person }) {
  const [values, setValues] = useState<Values>(() => valuesFromPerson(person));
  const [error, setError] = useState<string | null>(null);
  const update = useUpdatePerson();
  const notify = useNotify();
  const errorId = useId();

  const fields = changedFields(person, values);
  const dirty = Object.keys(fields).length > 0;

  function setValue(key: keyof Values, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
    setError(null);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (update.isPending || !dirty) return;
    const validationError = validate(values);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    update.mutate(
      { personId: person.id, fields },
      {
        // The page remounts this form from the saved Person (keyed by
        // `updated_at`), so the saved values become the new baseline.
        onSuccess: () => notify("success", "Данные профиля сохранены"),
        onError: (mutationError) => setError(profileSaveErrorMessage(mutationError)),
      },
    );
  }

  function handleCancel() {
    setValues(valuesFromPerson(person));
    setError(null);
  }

  const describedBy = error ? errorId : undefined;

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate aria-label="Данные профиля">
      <div className={styles.fieldGrid}>
        <Input
          label="Фамилия"
          name="last_name"
          autoComplete="family-name"
          value={values.last_name}
          onChange={(event) => setValue("last_name", event.target.value)}
          disabled={update.isPending}
          aria-describedby={describedBy}
          required
        />
        <Input
          label="Имя"
          name="first_name"
          autoComplete="given-name"
          value={values.first_name}
          onChange={(event) => setValue("first_name", event.target.value)}
          disabled={update.isPending}
          aria-describedby={describedBy}
          required
        />
        <Input
          label="Отчество"
          name="middle_name"
          autoComplete="additional-name"
          value={values.middle_name}
          onChange={(event) => setValue("middle_name", event.target.value)}
          disabled={update.isPending}
          aria-describedby={describedBy}
        />
        <Input
          label="Телефон"
          name="phone"
          type="tel"
          autoComplete="tel"
          value={values.phone}
          onChange={(event) => setValue("phone", event.target.value)}
          disabled={update.isPending}
          aria-describedby={describedBy}
        />
        <div className={styles.fullWidth}>
          <Input
            label="Адрес"
            name="address"
            autoComplete="street-address"
            value={values.address}
            onChange={(event) => setValue("address", event.target.value)}
            disabled={update.isPending}
            aria-describedby={describedBy}
          />
        </div>
      </div>

      <dl className={styles.readOnly}>
        <div className={styles.readOnlyRow}>
          <dt>Email</dt>
          <dd>{person.email ?? "—"}</dd>
        </div>
        <div className={styles.readOnlyRow}>
          <dt>Дата рождения</dt>
          <dd>{person.birth_date ? formatCalendarDate(person.birth_date) : "—"}</dd>
        </div>
      </dl>
      <p className={styles.note}>Email и дату рождения может изменить администратор клуба.</p>

      {error ? (
        <p className={styles.error} role="alert" id={errorId}>
          {error}
        </p>
      ) : null}

      <div className={styles.actions}>
        <Button type="submit" variant="primary" disabled={!dirty || update.isPending}>
          {update.isPending ? "Сохранение…" : "Сохранить"}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={handleCancel}
          disabled={!dirty || update.isPending}
        >
          Отмена
        </Button>
      </div>
    </form>
  );
}
