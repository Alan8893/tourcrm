import { useState } from "react";
import { useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { useNotify } from "../components/ui/notificationContext";
import {
  useArchiveGroup,
  useGroup,
  useGroupMembers,
  useGroupSchedule,
} from "../api/groups";
import { useMembershipPersonName } from "../api/people";
import { groupStatusIcon, groupStatusLabel, eventStatusIcon, eventStatusLabel } from "../domain/statusMapping";
import type { EventStatus } from "../domain/statusMapping";
import styles from "./GroupDetailPage.module.css";

export function GroupDetailPage() {
  const { groupId } = useParams<{ groupId: string }>();
  const groupQuery = useGroup(groupId);
  const [activeTab, setActiveTab] = useState("overview");
  const [confirmArchive, setConfirmArchive] = useState(false);
  const archiveGroup = useArchiveGroup();
  const notify = useNotify();

  if (groupQuery.isLoading) {
    return <Loading label="Загружаем группу…" />;
  }

  if (groupQuery.isError) {
    return (
      <ErrorState
        illustration={groupQuery.error.status === 404 ? "404" : "error"}
        title={groupQuery.error.status === 404 ? "Группа не найдена" : "Не удалось загрузить группу"}
        description={groupQuery.error.message}
      />
    );
  }

  const group = groupQuery.data;
  if (!group) return null;

  return (
    <div>
      <PageHeader
        title={group.name}
        back={{ to: "/groups", label: "Все группы" }}
        titleExtra={
          <StatusBadge status={groupStatusIcon(group.status)} label={groupStatusLabel(group.status)} />
        }
        actions={
          group.status === "active" ? (
            <Button variant="destructive" onClick={() => setConfirmArchive(true)}>
              Архивировать
            </Button>
          ) : undefined
        }
      />

      <Tabs
        label="Разделы группы"
        activeId={activeTab}
        onChange={setActiveTab}
        items={[
          {
            id: "overview",
            label: "Обзор",
            content: <OverviewTab description={group.description} validFrom={group.valid_from} validTo={group.valid_to} />,
          },
          {
            id: "members",
            label: "Участники",
            content: <MembersTab groupId={group.id} />,
          },
          {
            id: "schedule",
            label: "Расписание",
            content: <ScheduleTab groupId={group.id} />,
          },
        ]}
      />

      <ConfirmDialog
        open={confirmArchive}
        title="Архивировать группу?"
        description={`«${group.name}» перестанет отображаться в активном списке.`}
        confirmLabel="Архивировать"
        destructive
        pending={archiveGroup.isPending}
        onCancel={() => setConfirmArchive(false)}
        onConfirm={() =>
          archiveGroup.mutate(group.id, {
            onSuccess: () => {
              notify("success", `Группа «${group.name}» отправлена в архив`);
              setConfirmArchive(false);
            },
            onError: (error) => notify("error", error.message),
          })
        }
      />
    </div>
  );
}

function OverviewTab({
  description,
  validFrom,
  validTo,
}: {
  description: string | null;
  validFrom: string;
  validTo: string | null;
}) {
  return (
    <div>
      <p>{description || "Описание не указано."}</p>
      <div className={styles.metaRow}>
        <div>
          <span className={styles.metaLabel}>Действует с</span>
          {new Date(validFrom).toLocaleDateString("ru-RU")}
        </div>
        {validTo ? (
          <div>
            <span className={styles.metaLabel}>Действует по</span>
            {new Date(validTo).toLocaleDateString("ru-RU")}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MembersTab({ groupId }: { groupId: string }) {
  const membersQuery = useGroupMembers(groupId);

  if (membersQuery.isLoading) return <Loading label="Загружаем участников…" />;
  if (membersQuery.isError) {
    return <ErrorState illustration="error" title="Не удалось загрузить участников" description={membersQuery.error.message} />;
  }
  if (!membersQuery.data || membersQuery.data.items.length === 0) {
    return (
      <EmptyState
        illustration="empty-groups"
        title="В группе пока нет участников"
        description="Добавление участников выполняется в разделе «Люди»."
      />
    );
  }
  return (
    <ul className={styles.list}>
      {membersQuery.data.items.map((membership) => (
        <li key={membership.id} className={styles.memberRow}>
          <MemberName clubMembershipId={membership.club_membership_id} />
          <StatusBadge
            status={membership.membership_status === "active" ? "status.ongoing" : "status.ended"}
            label={membership.membership_status === "active" ? "Активно" : "Завершено"}
          />
        </li>
      ))}
    </ul>
  );
}

function MemberName({ clubMembershipId }: { clubMembershipId: string }) {
  const { isLoading, isError, name } = useMembershipPersonName(clubMembershipId);
  if (isLoading) return <span>Загрузка…</span>;
  if (isError || !name) return <span>Участник недоступен</span>;
  return <span>{name}</span>;
}

function ScheduleTab({ groupId }: { groupId: string }) {
  const scheduleQuery = useGroupSchedule(groupId);

  if (scheduleQuery.isLoading) return <Loading label="Загружаем расписание…" />;
  if (scheduleQuery.isError) {
    return <ErrorState illustration="error" title="Не удалось загрузить расписание" description={scheduleQuery.error.message} />;
  }
  if (!scheduleQuery.data || scheduleQuery.data.items.length === 0) {
    return (
      <EmptyState
        illustration="empty-groups"
        title="Пока нет мероприятий"
        description="Здесь появятся ближайшие и недавние события группы."
      />
    );
  }
  return (
    <ul className={styles.list}>
      {scheduleQuery.data.items.map((item) => (
        <li key={item.id} className={styles.memberRow}>
          <span>{item.title}</span>
          <StatusBadge
            status={eventStatusIcon(item.status as EventStatus)}
            label={eventStatusLabel(item.status as EventStatus)}
          />
        </li>
      ))}
    </ul>
  );
}
