import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { SearchInput } from "../components/ui/SearchInput";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Dialog } from "../components/ui/Dialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { ObjectListItem } from "../components/ui/ObjectListItem";
import { Pagination } from "../components/ui/Pagination";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser } from "../api/auth";
import { usePersons, useCreatePerson, personFullName } from "../api/people";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import styles from "./PeoplePage.module.css";

function formatBirthDate(birthDate: string | null): string | null {
  if (!birthDate) return null;
  return `Дата рождения: ${new Date(birthDate).toLocaleDateString("ru-RU")}`;
}

export function PeoplePage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const debouncedSearch = useDebouncedValue(search, 300);

  // A new search term always restarts from page 1 — an existing page
  // number for the previous result set would otherwise silently point
  // past the end of the new, narrower one.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const peopleQuery = usePersons({ page, search: debouncedSearch });
  const meQuery = useCurrentUser();
  // `person.create` is admin-only and unconditional (ADR-0035 §2) — no
  // self/other ambiguity to resolve, unlike most other People
  // authorization checks, so a plain role check is both correct and
  // sufficient here. The backend remains the actual enforcement point;
  // this only decides whether to offer the button at all.
  const canCreatePerson = meQuery.data?.role_assignments.some(
    (assignment) => assignment.role_code === "admin",
  );

  return (
    <div>
      <PageHeader
        title="Люди"
        description="Участники и представители клуба."
        actions={
          canCreatePerson ? (
            <Button variant="primary" icon="action.add" onClick={() => setCreateOpen(true)}>
              Добавить человека
            </Button>
          ) : undefined
        }
      />

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <SearchInput
            label="Поиск по имени"
            value={search}
            onChange={setSearch}
            placeholder="Например, «Иванова»"
          />
        </div>
      </div>

      {peopleQuery.isLoading ? <Loading label="Загружаем людей…" /> : null}

      {peopleQuery.isError ? (
        <ErrorState
          illustration={peopleQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить людей"
          description={peopleQuery.error.message}
        />
      ) : null}

      {peopleQuery.isSuccess && peopleQuery.data.items.length === 0 ? (
        <EmptyState
          illustration={debouncedSearch ? "no-results" : "empty-people"}
          title={debouncedSearch ? "Ничего не найдено" : "Пока нет ни одного человека"}
          description={
            debouncedSearch
              ? "Попробуйте изменить запрос поиска."
              : "Здесь появятся участники клуба."
          }
        />
      ) : null}

      {peopleQuery.isSuccess && peopleQuery.data.items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {peopleQuery.data.items.map((person) => (
              <li key={person.id}>
                <ObjectListItem
                  title={personFullName(person)}
                  to={`/people/${person.id}`}
                  description={formatBirthDate(person.birth_date)}
                />
              </li>
            ))}
          </ul>
          <Pagination
            page={peopleQuery.data.pagination.page}
            pages={peopleQuery.data.pagination.pages}
            total={peopleQuery.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}

      <CreatePersonDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

function CreatePersonDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [address, setAddress] = useState("");
  const createPerson = useCreatePerson();
  const notify = useNotify();
  const navigate = useNavigate();

  function reset() {
    setFirstName("");
    setLastName("");
    setMiddleName("");
    setPhone("");
    setEmail("");
    setAddress("");
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    if (!firstName.trim() || !lastName.trim()) return;
    createPerson.mutate(
      {
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        middle_name: middleName.trim() || undefined,
        phone: phone.trim() || undefined,
        email: email.trim() || undefined,
        address: address.trim() || undefined,
      },
      {
        onSuccess: (person) => {
          notify("success", `Человек «${lastName.trim()} ${firstName.trim()}» добавлен`);
          reset();
          onClose();
          navigate(`/people/${person.id}`);
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Новый человек"
      description="Личность создаётся отдельно от членства в клубе — привязать её к клубу можно после."
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={createPerson.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!firstName.trim() || !lastName.trim() || createPerson.isPending}
          >
            Создать
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input
          label="Фамилия"
          value={lastName}
          onChange={(event) => setLastName(event.target.value)}
          required
        />
        <Input
          label="Имя"
          value={firstName}
          onChange={(event) => setFirstName(event.target.value)}
          required
        />
        <Input
          label="Отчество"
          value={middleName}
          onChange={(event) => setMiddleName(event.target.value)}
        />
        <Input label="Телефон" value={phone} onChange={(event) => setPhone(event.target.value)} />
        <Input
          label="Email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Input
          label="Адрес"
          value={address}
          onChange={(event) => setAddress(event.target.value)}
        />
      </div>
    </Dialog>
  );
}
