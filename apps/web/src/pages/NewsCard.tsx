import { Link } from "react-router-dom";

import { LinkCard } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { newsImageUrl, type News } from "../api/news";
import { formatEventDate, formatNewsDate } from "../domain/newsFormat";
import styles from "./News.module.css";

/** One News card for Home and «Все новости» — with an image when the
 * News has one, otherwise a text-only card with the brand accent strip.
 * Presentation only: the item list itself comes from the backend. */
export function NewsCard({ news }: { news: News }) {
  const imageUrl = newsImageUrl(news);
  return (
    <LinkCard to={`/news/${news.id}`} className={styles.card} data-testid="news-card">
      {imageUrl ? (
        <img className={styles.cardImage} src={imageUrl} alt="" loading="lazy" />
      ) : (
        <div className={styles.cardAccent} aria-hidden="true" />
      )}
      <div className={styles.cardBody}>
        <div className={styles.cardTitle}>{news.title}</div>
        <div className={styles.meta}>
          {news.published_at ? <span>{formatNewsDate(news.published_at)}</span> : null}
          {news.event_date ? <span>Дата события: {formatEventDate(news.event_date)}</span> : null}
          {news.location ? <span>{news.location}</span> : null}
        </div>
        <p className={styles.excerpt}>{news.body}</p>
      </div>
    </LinkCard>
  );
}

/** Contextual Administrator entry into News management (Issue #227) —
 * never a global navigation item. */
export function ManageNewsLink() {
  return (
    <Link to="/news/manage">
      <Button variant="secondary" icon="action.edit">
        Управление новостями
      </Button>
    </Link>
  );
}
