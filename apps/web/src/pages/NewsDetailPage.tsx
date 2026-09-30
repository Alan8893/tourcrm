import { Link, useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Loading } from "../components/ui/Loading";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useCurrentUser } from "../api/auth";
import { newsImageUrl, useNews } from "../api/news";
import { formatDayMonth, formatTime } from "../domain/calendarDate";
import { eventNavigationPath, formatEventDate, formatNewsDate } from "../domain/newsFormat";
import { newsStatusIcon, newsStatusLabel } from "../domain/statusMapping";
import { hasAdministratorRole } from "../shell/navigation";
import styles from "./News.module.css";

/** News detail (TH-0120 / Issue #227). The backend answers 404 for a
 * News the user may not see (other audience, draft, archived) — so a
 * direct URL never exposes it; this page just renders that as "not
 * found". */
export function NewsDetailPage() {
  const { newsId } = useParams();
  const newsQuery = useNews(newsId);
  const meQuery = useCurrentUser();
  const isAdmin = hasAdministratorRole(meQuery.data?.role_assignments ?? []);

  if (newsQuery.isLoading) {
    return <Loading label="Загружаем новость…" />;
  }

  if (newsQuery.isError) {
    const notFound = newsQuery.error.status === 404;
    return (
      <ErrorState
        illustration={notFound ? "404" : newsQuery.error.status === 403 ? "403" : "error"}
        title={notFound ? "Новость не найдена" : "Не удалось загрузить новость"}
        description={
          notFound ? "Возможно, она снята с публикации или недоступна вам." : newsQuery.error.message
        }
        action={
          <Link to="/news">
            <Button variant="primary">Все новости</Button>
          </Link>
        }
      />
    );
  }

  const news = newsQuery.data;
  if (!news) return null;
  const imageUrl = newsImageUrl(news);

  return (
    <div>
      <PageHeader
        title={news.title}
        back={{ to: "/news", label: "Все новости" }}
        titleExtra={
          isAdmin ? (
            <StatusBadge status={newsStatusIcon(news.status)} label={newsStatusLabel(news.status)} />
          ) : undefined
        }
        actions={
          isAdmin && news.status !== "archived" ? (
            <Link to={`/news/manage/${news.id}/edit`}>
              <Button variant="secondary" icon="action.edit">
                Редактировать
              </Button>
            </Link>
          ) : undefined
        }
      />
      <article className={styles.detail}>
        {imageUrl ? <img className={styles.detailImage} src={imageUrl} alt="" /> : null}
        <dl className={styles.facts}>
          {news.published_at ? (
            <div>
              <dt>Опубликовано</dt>
              <dd>{formatNewsDate(news.published_at)}</dd>
            </div>
          ) : null}
          {news.event_date ? (
            <div>
              <dt>Дата события</dt>
              <dd>{formatEventDate(news.event_date)}</dd>
            </div>
          ) : null}
          {news.location ? (
            <div>
              <dt>Место</dt>
              <dd>{news.location}</dd>
            </div>
          ) : null}
        </dl>
        <p className={styles.body}>{news.body}</p>
        {news.linked_event ? (
          <Card>
            <div className={styles.eventLink}>
              <div>
                <div className={styles.label}>Связанное событие</div>
                <div className={styles.cardTitle}>{news.linked_event.title}</div>
                <div className={styles.meta}>
                  {formatDayMonth(news.linked_event.start_at)}, {formatTime(news.linked_event.start_at)}
                </div>
              </div>
              <Link to={eventNavigationPath(news.linked_event)}>
                <Button variant="primary" icon="nav.events">
                  Перейти к событию
                </Button>
              </Link>
            </div>
          </Card>
        ) : null}
      </article>
    </div>
  );
}
