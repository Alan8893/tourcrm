import { useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { FilterSelect } from "../components/ui/FilterSelect";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import {
  useArchiveNews,
  useNewsList,
  usePublishNews,
  type News,
  type NewsListStatus,
} from "../api/news";
import { newsStatusIcon, newsStatusLabel } from "../domain/statusMapping";
import { formatNewsDate } from "../domain/newsFormat";
import styles from "./News.module.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS: { value: NewsListStatus; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "draft", label: "Черновики" },
  { value: "published", label: "Опубликованные" },
  { value: "archived", label: "Архив" },
];

/** «Управление новостями» (TH-0120 / Issue #227): Administrator-only
 * management page, reached only through the contextual action on Home and
 * «Все новости» — no global navigation item. Route-guarded by
 * `AdministratorGuard`; the backend enforces Administrator-only mutation
 * regardless. Create/edit happen on their own page, not in a modal. */
export function NewsManagePage() {
  const [status, setStatus] = useState<NewsListStatus>("all");
  const [page, setPage] = useState(1);
  const [publishTarget, setPublishTarget] = useState<News | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<News | null>(null);
  const newsQuery = useNewsList({ status, page, pageSize: PAGE_SIZE });
  const publish = usePublishNews();
  const archive = useArchiveNews();
  const notify = useNotify();

  return (
    <div>
      <PageHeader
        title="Управление новостями"
        description="Черновики, опубликованные и архивные новости клуба."
        back={{ to: "/news", label: "Все новости" }}
        actions={
          <Link to="/news/manage/new">
            <Button variant="primary" icon="action.add">
              Создать новость
            </Button>
          </Link>
        }
      />

      <div className={styles.toolbar}>
        <FilterSelect
          label="Статус"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => {
            setStatus(value as NewsListStatus);
            setPage(1);
          }}
        />
      </div>

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
          title="Новостей нет"
          description="Создайте новость — она сохранится как черновик или будет опубликована сразу."
        />
      ) : null}

      {newsQuery.isSuccess && newsQuery.data.items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {newsQuery.data.items.map((news) => (
              <li key={news.id}>
                <Card>
                  <div className={styles.manageRow}>
                    <div className={styles.manageInfo}>
                      <Link to={`/news/${news.id}`} className={styles.cardTitle}>
                        {news.title}
                      </Link>
                      <div className={styles.meta}>
                        <StatusBadge
                          status={newsStatusIcon(news.status)}
                          label={newsStatusLabel(news.status)}
                        />
                        <span>
                          {news.published_at
                            ? `Опубликовано ${formatNewsDate(news.published_at)}`
                            : `Создано ${formatNewsDate(news.created_at)}`}
                        </span>
                        <span>
                          {news.audience_type === "club" ? "Весь клуб" : "Выбранные группы"}
                        </span>
                      </div>
                    </div>
                    <div className={styles.manageActions}>
                      {news.status !== "archived" ? (
                        <Link to={`/news/manage/${news.id}/edit`}>
                          <Button variant="secondary" icon="action.edit">
                            Изменить
                          </Button>
                        </Link>
                      ) : null}
                      {news.status === "draft" ? (
                        <Button variant="primary" onClick={() => setPublishTarget(news)}>
                          Опубликовать
                        </Button>
                      ) : null}
                      {news.status !== "archived" ? (
                        <Button
                          variant="secondary"
                          icon="action.archive"
                          onClick={() => setArchiveTarget(news)}
                        >
                          Архивировать
                        </Button>
                      ) : null}
                    </div>
                  </div>
                </Card>
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

      <ConfirmDialog
        open={Boolean(publishTarget)}
        title="Опубликовать новость?"
        description={
          publishTarget
            ? `«${publishTarget.title}» станет видна выбранной аудитории.`
            : undefined
        }
        confirmLabel="Опубликовать"
        pending={publish.isPending}
        onCancel={() => setPublishTarget(null)}
        onConfirm={() => {
          if (!publishTarget) return;
          publish.mutate(publishTarget.id, {
            onSuccess: () => {
              notify("success", `Новость «${publishTarget.title}» опубликована`);
              setPublishTarget(null);
            },
            onError: (error) => notify("error", error.message),
          });
        }}
      />
      <ConfirmDialog
        open={Boolean(archiveTarget)}
        title="Архивировать новость?"
        description={
          archiveTarget
            ? `«${archiveTarget.title}» перестанет отображаться в новостях. Запись сохранится в архиве.`
            : undefined
        }
        confirmLabel="Архивировать"
        destructive
        pending={archive.isPending}
        onCancel={() => setArchiveTarget(null)}
        onConfirm={() => {
          if (!archiveTarget) return;
          archive.mutate(archiveTarget.id, {
            onSuccess: () => {
              notify("success", `Новость «${archiveTarget.title}» перенесена в архив`);
              setArchiveTarget(null);
            },
            onError: (error) => notify("error", error.message),
          });
        }}
      />
    </div>
  );
}
