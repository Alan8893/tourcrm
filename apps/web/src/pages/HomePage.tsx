import { Link } from "react-router-dom";

import { Card } from "../components/ui/Card";
import { Button } from "../components/ui/Button";
import { Avatar } from "../components/ui/Avatar";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useCurrentUser, displayName } from "../api/auth";
import { useUpcomingEvents } from "../api/events";
import { personFullName, useMyChildren, type Child } from "../api/people";
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
  // PO decision (PR #127, TH-0104): Guardian "Мои дети" lives as a
  // role-gated contextual Home section, exactly like "Ближайшие события"
  // — never a new top-level nav item (NAVIGATION_ITEMS is closed) and
  // never a new ProfileMenu entry or route.
  const isGuardian = meQuery.data?.role_assignments.some((a) => a.role_code === "guardian") ?? false;

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

      {isGuardian ? <MyChildrenSection /> : null}

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

/** Guardian-only "Мои дети" section (TH-0104, PR #127 PO decision):
 * mirrors "Ближайшие события" above exactly (own heading, loading/error/
 * empty/success states, `Card` rows). Data comes exclusively from
 * `GET /me/children` — the canonical safe projection (id, last_name,
 * first_name, middle_name, birth_date, photo_file_id) — and nothing here
 * enriches it with a further `usePerson(child.id)` call; no contacts, no
 * other GuardianRelationship data ever appear.
 */
function MyChildrenSection() {
  const childrenQuery = useMyChildren();

  return (
    <section>
      <h2 className={styles.sectionTitle}>Мои дети</h2>

      {childrenQuery.isLoading ? <Loading label="Загружаем детей…" /> : null}

      {childrenQuery.isError ? (
        <ErrorState
          illustration={childrenQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить данные о детях"
          description={childrenQuery.error.message}
          action={
            <Button variant="secondary" onClick={() => childrenQuery.refetch()}>
              Повторить
            </Button>
          }
        />
      ) : null}

      {childrenQuery.isSuccess && childrenQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="no-results"
          title="Дети не найдены"
          description="Здесь появятся дети, для которых вы указаны законным представителем."
        />
      ) : null}

      {childrenQuery.isSuccess && childrenQuery.data.items.length > 0 ? (
        <ul className={styles.eventList}>
          {childrenQuery.data.items.map((child) => (
            <li key={child.id}>
              <ChildCard child={child} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function ChildCard({ child }: { child: Child }) {
  const name = personFullName(child);
  return (
    <Card>
      <div className={styles.eventRow}>
        <div className={styles.childInfo}>
          <Avatar name={name} />
          <div>
            <div className={styles.eventTitle}>{name}</div>
            <div className={styles.eventTime}>
              {child.birth_date
                ? new Date(child.birth_date).toLocaleDateString("ru-RU")
                : "Дата рождения не указана"}
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}
