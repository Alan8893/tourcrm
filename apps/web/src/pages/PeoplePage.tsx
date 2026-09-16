import { useEffect, useState } from "react";

import { PageHeader } from "../components/ui/PageHeader";
import { SearchInput } from "../components/ui/SearchInput";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { ObjectListItem } from "../components/ui/ObjectListItem";
import { Pagination } from "../components/ui/Pagination";
import { usePersons, personFullName } from "../api/people";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import styles from "./PeoplePage.module.css";

function formatBirthDate(birthDate: string | null): string | null {
  if (!birthDate) return null;
  return `Дата рождения: ${new Date(birthDate).toLocaleDateString("ru-RU")}`;
}

export function PeoplePage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(search, 300);

  // A new search term always restarts from page 1 — an existing page
  // number for the previous result set would otherwise silently point
  // past the end of the new, narrower one.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const peopleQuery = usePersons({ page, search: debouncedSearch });

  return (
    <div>
      <PageHeader title="Люди" description="Участники и представители клуба." />

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
    </div>
  );
}
