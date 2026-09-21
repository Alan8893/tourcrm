import { useState } from "react";
import { useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { SearchInput } from "../components/ui/SearchInput";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { useNotify } from "../components/ui/notificationContext";
import {
  useAddGroupMember,
  useArchiveGroup,
  useGroup,
  useGroupMembers,
  useGroupSchedule,
} from "../api/groups";
import { useCurrentUser } from "../api/auth";
import { useMembershipPersonName, usePersons, personFullName } from "../api/people";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
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
  const meQuery = useCurrentUser();
  // group.manage is admin-only in the current MVP (people-api.md §14) —
  // same UX-only convention as PeoplePage/PersonDetailPage's isAdmin.
  const isAdmin = meQuery.data?.role_assignments.some((a) => a.role_code === "admin") ?? false;

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
            content: <MembersTab groupId={group.id} isAdmin={isAdmin} />,
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

function MembersTab({ groupId, isAdmin }: { groupId: string; isAdmin: boolean }) {
  const membersQuery = useGroupMembers(groupId);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div>
      {isAdmin ? (
        <div className={styles.tabActions}>
          <Button variant="secondary" icon="action.add" onClick={() => setAddOpen(true)}>
            Добавить участника
          </Button>
        </div>
      ) : null}

      {membersQuery.isLoading ? <Loading label="Загружаем участников…" /> : null}
      {membersQuery.isError ? (
        <ErrorState
          illustration="error"
          title="Не удалось загрузить участников"
          description={membersQuery.error.message}
        />
      ) : null}
      {membersQuery.isSuccess && membersQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="empty-groups"
          title="В группе пока нет участников"
          description="Добавьте первого участника группы."
        />
      ) : null}
      {membersQuery.isSuccess && membersQuery.data.items.length > 0 ? (
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
      ) : null}

      <AddParticipantDialog open={addOpen} onClose={() => setAddOpen(false)} groupId={groupId} />
    </div>
  );
}

function AddParticipantDialog({
  open,
  onClose,
  groupId,
}: {
  open: boolean;
  onClose: () => void;
  groupId: string;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const searchQuery = usePersons({ page: 1, search: debouncedSearch });
  const addMember = useAddGroupMember();
  const notify = useNotify();

  function handleClose() {
    setSearch("");
    onClose();
  }

  function handleAdd(personId: string) {
    addMember.mutate(
      { groupId, personId },
      {
        onSuccess: () => {
          notify("success", "Участник добавлен в группу");
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Добавить участника"
      onClose={handleClose}
      actions={
        <Button variant="secondary" onClick={handleClose} disabled={addMember.isPending}>
          Отмена
        </Button>
      }
    >
      <div className={styles.form}>
        <SearchInput
          label="Поиск человека"
          value={search}
          onChange={setSearch}
          placeholder="Например, «Иванова»"
        />
        {searchQuery.isSuccess && debouncedSearch ? (
          <ul className={styles.pickerList}>
            {searchQuery.data.items.map((candidate) => (
              <li key={candidate.id}>
                <button
                  type="button"
                  className={styles.pickerItem}
                  disabled={addMember.isPending}
                  onClick={() => handleAdd(candidate.id)}
                >
                  {personFullName(candidate)}
                </button>
              </li>
            ))}
            {searchQuery.data.items.length === 0 ? (
              <li className={styles.pickerEmpty}>Ничего не найдено</li>
            ) : null}
          </ul>
        ) : null}
      </div>
    </Dialog>
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
