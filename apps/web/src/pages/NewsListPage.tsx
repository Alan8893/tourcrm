import { useState } from "react";

import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Pagination } from "../components/ui/Pagination";
import { useCurrentUser } from "../api/auth";
import { useNewsList } from "../api/news";
import { hasAdministratorRole } from "../shell/navigation";
import { ManageNewsLink, NewsCard } from "./NewsCard";
import styles from "./News.module.css";

const PAGE_SIZE = 12;

/** «Все новости» (TH-0120 / Issue #227): every published News the
 * backend returns for the signed-in user — the page applies no audience
 * logic of its own. Reached from Home; no global navigation item. */
export function NewsListPage() {
  const [page, setPage] = useState(1);
  const meQuery = useCurrentUser();
  const isAdmin = hasAdministratorRole(meQuery.data?.role_assignments ?? []);
  const newsQuery = useNewsList({ status: "published", page, pageSize: PAGE_SIZE });

  return (
    <div>
      <PageHeader
        title="Новости"
        description="Объявления клуба о событиях и жизни клуба."
        back={{ to: "/", label: "На главную" }}
        actions={isAdmin ? <ManageNewsLink /> : undefined}
      />

      {newsQuery.isLoading ? <Loading label="Загружаем новости…" /> : null}

      {newsQuery.isError ? (
        <ErrorState
          illustration={newsQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить новости"
          description={newsQuery.error.message}
          action={
            <Button variant="secondary" onClick={() => newsQuery.refetch()}>
              Повторить
            </Button>
          }
        />
      ) : null}

      {newsQuery.isSuccess && newsQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="no-results"
          title="Новостей пока нет"
          description="Здесь появятся объявления клуба."
        />
      ) : null}

      {newsQuery.isSuccess && newsQuery.data.items.length > 0 ? (
        <>
          <ul className={styles.grid}>
            {newsQuery.data.items.map((news) => (
              <li key={news.id}>
                <NewsCard news={news} />
              </li>
            ))}
          </ul>
          <div className={styles.pagination}>
            <Pagination
              page={newsQuery.data.pagination.page}
              pages={newsQuery.data.pagination.pages}
              total={newsQuery.data.pagination.total}
              onPageChange={setPage}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}
