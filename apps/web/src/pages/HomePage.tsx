import { Link } from "react-router-dom";

import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useCurrentUser, displayName } from "../api/auth";
import { useUpcomingEvents } from "../api/events";
import { eventStatusIcon, eventStatusLabel } from "../domain/statusMapping";
import styles from "./HomePage.module.css";

/**
 * Reference Home composition (spec §13): current context, nearest
 * events, quick actions — deliberately not a KPI dashboard. All data
 * shown is real (auth/me, events list); there is no fabricated summary
 * metric here precisely because nothing in the current backend contract
 * would back one honestly.
 */
export function HomePage() {
  const meQuery = useCurrentUser();
  const upcomingQuery = useUpcomingEvents(5);

  const name = meQuery.data ? displayName(meQuery.data.user) : null;

  return (
    <div>
      <h1 className={styles.greeting}>{name ? `Здравствуйте, ${name}!` : "Добро пожаловать в TourCRM"}</h1>
      <p className={styles.subtitle}>Вот что происходит в клубе прямо сейчас.</p>

      <div className={styles.quickActions}>
        <Link to="/groups">
          <Button variant="primary" icon="nav.groups">
            Перейти к группам
          </Button>
        </Link>
        <Link to="/events">
          <Button variant="secondary" icon="nav.events">
            Все события
          </Button>
        </Link>
      </div>

      <h2 className={styles.sectionTitle}>Ближайшие события</h2>

      {upcomingQuery.isLoading ? <Loading label="Загружаем события…" /> : null}

      {upcomingQuery.isError ? (
        <ErrorState
          illustration={upcomingQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить события"
          description={upcomingQuery.error.message}
        />
      ) : null}

      {upcomingQuery.isSuccess && upcomingQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="no-results"
          title="Ближайших событий пока нет"
          description="Как только появятся запланированные мероприятия, они отобразятся здесь."
        />
      ) : null}

      {upcomingQuery.isSuccess && upcomingQuery.data.items.length > 0 ? (
        <ul className={styles.eventList}>
          {upcomingQuery.data.items.map((event) => (
            <li key={event.id}>
              <Card>
                <div className={styles.eventRow}>
                  <div>
                    <div className={styles.eventTitle}>{event.title}</div>
                    <div className={styles.eventTime}>
                      {new Date(event.start_at).toLocaleString("ru-RU", {
                        day: "numeric",
                        month: "long",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </div>
                  </div>
                  <StatusBadge status={eventStatusIcon(event.status)} label={eventStatusLabel(event.status)} />
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
