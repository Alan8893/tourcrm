import { useState } from "react";
import { useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import {
  personFullName,
  usePerson,
  usePersonGuardianRelationships,
  usePersonMemberships,
  type GuardianRelationship,
} from "../api/people";
import {
  membershipStatusIcon,
  membershipStatusLabel,
  guardianRelationshipStatusIcon,
  guardianRelationshipStatusLabel,
} from "../domain/statusMapping";
import styles from "./PersonDetailPage.module.css";

export function PersonDetailPage() {
  const { personId } = useParams<{ personId: string }>();
  const personQuery = usePerson(personId);
  const [activeTab, setActiveTab] = useState("overview");

  if (personQuery.isLoading) {
    return <Loading label="Загружаем данные…" />;
  }

  if (personQuery.isError) {
    return (
      <ErrorState
        illustration={personQuery.error.status === 404 ? "404" : "error"}
        title={personQuery.error.status === 404 ? "Человек не найден" : "Не удалось загрузить данные"}
        description={personQuery.error.message}
      />
    );
  }

  const person = personQuery.data;
  if (!person) return null;

  return (
    <div>
      <PageHeader title={personFullName(person)} back={{ to: "/people", label: "Все люди" }} />

      <Tabs
        label="Разделы профиля"
        activeId={activeTab}
        onChange={setActiveTab}
        items={[
          {
            id: "overview",
            label: "Обзор",
            content: <OverviewTab birthDate={person.birth_date} />,
          },
          {
            id: "memberships",
            label: "Членство",
            content: <MembershipsTab personId={person.id} />,
          },
          {
            id: "guardians",
            label: "Представители",
            content: <GuardiansTab personId={person.id} />,
          },
        ]}
      />
    </div>
  );
}

function OverviewTab({ birthDate }: { birthDate: string | null }) {
  return (
    <div className={styles.metaRow}>
      <div>
        <span className={styles.metaLabel}>Дата рождения</span>
        {birthDate ? new Date(birthDate).toLocaleDateString("ru-RU") : "Не указана"}
      </div>
    </div>
  );
}

function MembershipsTab({ personId }: { personId: string }) {
  const membershipsQuery = usePersonMemberships(personId);

  if (membershipsQuery.isLoading) return <Loading label="Загружаем членство…" />;
  if (membershipsQuery.isError) {
    return (
      <ErrorState
        illustration="error"
        title="Не удалось загрузить членство"
        description={membershipsQuery.error.message}
      />
    );
  }
  if (!membershipsQuery.data || membershipsQuery.data.items.length === 0) {
    return (
      <EmptyState
        illustration="empty-people"
        title="Нет данных о членстве"
        description="У этого человека пока нет периодов членства в клубе."
      />
    );
  }
  return (
    <ul className={styles.list}>
      {membershipsQuery.data.items.map((membership) => (
        <li key={membership.id} className={styles.row}>
          <div className={styles.rowMain}>
            <span>{membership.membership_type}</span>
            <span className={styles.rowSecondary}>
              {new Date(membership.joined_at).toLocaleDateString("ru-RU")}
              {membership.left_at
                ? ` — ${new Date(membership.left_at).toLocaleDateString("ru-RU")}`
                : " — по настоящее время"}
            </span>
          </div>
          <StatusBadge
            status={membershipStatusIcon(membership.status)}
            label={membershipStatusLabel(membership.status)}
          />
        </li>
      ))}
    </ul>
  );
}

function GuardiansTab({ personId }: { personId: string }) {
  const guardiansQuery = usePersonGuardianRelationships(personId);

  if (guardiansQuery.isLoading) return <Loading label="Загружаем представителей…" />;
  if (guardiansQuery.isError) {
    return (
      <ErrorState
        illustration="error"
        title="Не удалось загрузить представителей"
        description={guardiansQuery.error.message}
      />
    );
  }
  if (!guardiansQuery.data || guardiansQuery.data.items.length === 0) {
    return (
      <EmptyState
        illustration="empty-people"
        title="Законные представители не указаны"
        description="Для этого человека не найдено ни одной связи с представителем."
      />
    );
  }
  return (
    <ul className={styles.list}>
      {guardiansQuery.data.items.map((relationship) => (
        <GuardianRow key={relationship.id} relationship={relationship} />
      ))}
    </ul>
  );
}

function GuardianRow({ relationship }: { relationship: GuardianRelationship }) {
  const guardianQuery = usePerson(relationship.guardian_person_id);
  const name = guardianQuery.data ? personFullName(guardianQuery.data) : null;

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <span>
          {guardianQuery.isLoading ? "Загрузка…" : name ?? "Представитель недоступен"}
        </span>
        <span className={styles.rowSecondary}>
          {relationship.relationship_type}
          {relationship.is_primary_contact ? (
            <> · <span className={styles.primaryTag}>Основной контакт</span></>
          ) : null}
        </span>
      </div>
      <StatusBadge
        status={guardianRelationshipStatusIcon(relationship.status)}
        label={guardianRelationshipStatusLabel(relationship.status)}
      />
    </li>
  );
}
