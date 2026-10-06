import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { SearchInput } from "../components/ui/SearchInput";
import { FilterSelect } from "../components/ui/FilterSelect";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { useNotify } from "../components/ui/notificationContext";
import {
  useAddGroupMember,
  useAllGroups,
  useArchiveGroup,
  useEndGroupMembership,
  useGroup,
  useGroupMembers,
  useGroupSchedule,
  useTransferGroupMembership,
} from "../api/groups";
import type { GroupMembership } from "../api/groups";
import { useCurrentUser } from "../api/auth";
import type { ApiError } from "../api/client";
import { useMembershipPersonName, usePersons, personFullName } from "../api/people";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { groupStatusIcon, groupStatusLabel, eventStatusIcon, eventStatusLabel } from "../domain/statusMapping";
import { formatScheduleRange } from "../domain/calendarDate";
import { groupDetailTabIds } from "../domain/groupDetailTabs";
import type { GroupDetailTabId } from "../domain/groupDetailTabs";
import type { EventStatus } from "../domain/statusMapping";
import type { GroupsShortcutState } from "./GroupsPage";
import styles from "./GroupDetailPage.module.css";

/** A nested read (`/members`, `/schedule`) the backend does not grant this
 * user answers 403/404 under its own authorization contract — shown as
 * "no access" rather than as a failure. */
function isAccessDenied(error: ApiError): boolean {
  return error.status === 403 || error.status === 404;
}

export function GroupDetailPage() {
  const { groupId } = useParams<{ groupId: string }>();
  const groupQuery = useGroup(groupId);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const archiveGroup = useArchiveGroup();
  const notify = useNotify();
  const meQuery = useCurrentUser();
  // group.manage is admin-only in the current MVP (people-api.md §14) —
  // same UX-only convention as PeoplePage/PersonDetailPage's isAdmin.
  const roleCodes = meQuery.data?.role_assignments.map((a) => a.role_code) ?? [];
  const isAdmin = roleCodes.includes("admin");
  // Issue #285: role-aware tabs (UX only — every tab's data stays
  // authorized by the backend). The first visible tab is the default.
  const tabIds = groupDetailTabIds(roleCodes);
  const activeTab = selectedTab !== null && (tabIds as string[]).includes(selectedTab) ? selectedTab : tabIds[0];
  // Issue #282: reached through the Member single-group shortcut, the
  // Groups list is never shown — a "back to all groups" link would only
  // bounce back here.
  const location = useLocation();
  const viaGroupsShortcut =
    (location.state as Partial<GroupsShortcutState> | null)?.groupsShortcut === true;

  if (groupQuery.isLoading || meQuery.isLoading) {
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

  const groupTab = (id: GroupDetailTabId) => {
    switch (id) {
      case "overview":
        return {
          id,
          label: "Обзор",
          content: <OverviewTab description={group.description} validFrom={group.valid_from} validTo={group.valid_to} />,
        };
      case "members":
        return {
          id,
          label: "Участники",
          content: <MembersTab groupId={group.id} clubId={group.club_id} isAdmin={isAdmin} />,
        };
      case "schedule":
        return { id, label: "Расписание", content: <ScheduleTab groupId={group.id} /> };
    }
  };

  return (
    <div>
      <PageHeader
        title={group.name}
        back={viaGroupsShortcut ? undefined : { to: "/groups", label: "Все группы" }}
        titleExtra={
          <StatusBadge status={groupStatusIcon(group.status)} label={groupStatusLabel(group.status)} />
        }
        actions={
          <>
            {/* TH-0118.5 contextual action (import-export-ui.md §5.1):
                opens the Export master with this Group pre-selected.
                Participant Export is Administrator-only; Import into a
                Group is not offered — the Import MVP creates no
                GroupMembership. */}
            {isAdmin ? (
              <Link to={`/reports/export?context=group&group_id=${group.id}`}>
                <Button variant="secondary" icon="action.download">
                  Экспорт участников
                </Button>
              </Link>
            ) : null}
            {isAdmin && group.status === "active" ? (
              <Button variant="destructive" onClick={() => setConfirmArchive(true)}>
                Архивировать
              </Button>
            ) : null}
          </>
        }
      />

      <Tabs
        label="Разделы группы"
        activeId={activeTab}
        onChange={setSelectedTab}
        items={tabIds.map((id) => groupTab(id))}
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

function MembersTab({
  groupId,
  clubId,
  isAdmin,
}: {
  groupId: string;
  clubId: string;
  isAdmin: boolean;
}) {
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
        isAccessDenied(membersQuery.error) ? (
          <ErrorState
            illustration="403"
            title="Нет доступа"
            description="Список участников этой группы вам недоступен."
          />
        ) : (
          <ErrorState
            illustration="error"
            title="Не удалось загрузить участников"
            description={membersQuery.error.message}
          />
        )
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
              <span className={styles.memberRowActions}>
                <StatusBadge
                  status={membership.membership_status === "active" ? "status.ongoing" : "status.ended"}
                  label={membership.membership_status === "active" ? "Активно" : "Завершено"}
                />
                {/* Issue #286: managing a current membership is group.manage
                    (Administrator) only — UX only, the backend authorizes. */}
                {isAdmin && membership.membership_status === "active" ? (
                  <MembershipActions membership={membership} clubId={clubId} />
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <AddParticipantDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        groupId={groupId}
        clubId={clubId}
      />
    </div>
  );
}

function AddParticipantDialog({
  open,
  onClose,
  groupId,
  clubId,
}: {
  open: boolean;
  onClose: () => void;
  groupId: string;
  clubId: string;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  // TH-0116 / Issue #150: only people eligible to join this Group's Club
  // (an active ClubMembership) are offered — server-side filter via
  // people-api.md §4.1's `club_id`, never fetch-all + client filtering.
  const searchQuery = usePersons({ page: 1, search: debouncedSearch, clubId });
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

/** Issue #286: "Переместить" / "Удалить" for one current membership. Both
 * ask for confirmation; each is exactly ONE backend call (transfer is the
 * atomic `POST .../transfer`, never end + add from the client). */
function MembershipActions({ membership, clubId }: { membership: GroupMembership; clubId: string }) {
  const [dialog, setDialog] = useState<"transfer" | "remove" | null>(null);
  const { name } = useMembershipPersonName(membership.club_membership_id);
  const who = name ?? "Участник";

  return (
    <>
      <Button variant="secondary" onClick={() => setDialog("transfer")}>
        Переместить
      </Button>
      <Button variant="destructive" onClick={() => setDialog("remove")}>
        Удалить
      </Button>
      {dialog === "remove" ? (
        <RemoveMemberDialog membership={membership} who={who} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === "transfer" ? (
        <TransferMemberDialog
          membership={membership}
          clubId={clubId}
          who={who}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  );
}

function RemoveMemberDialog({
  membership,
  who,
  onClose,
}: {
  membership: GroupMembership;
  who: string;
  onClose: () => void;
}) {
  const endMembership = useEndGroupMembership();
  const notify = useNotify();

  return (
    <ConfirmDialog
      open
      title="Удалить из группы?"
      description={`${who} больше не будет участником этой группы. История участия сохранится.`}
      confirmLabel="Удалить"
      destructive
      pending={endMembership.isPending}
      onCancel={onClose}
      onConfirm={() =>
        endMembership.mutate(
          { membershipId: membership.id, groupId: membership.group_id },
          {
            onSuccess: () => {
              notify("success", `${who}: удалён(а) из группы`);
              onClose();
            },
            onError: (error) => notify("error", error.message),
          },
        )
      }
    />
  );
}

function TransferMemberDialog({
  membership,
  clubId,
  who,
  onClose,
}: {
  membership: GroupMembership;
  clubId: string;
  who: string;
  onClose: () => void;
}) {
  const groupsQuery = useAllGroups("active");
  const transfer = useTransferGroupMembership();
  const notify = useNotify();
  const [targetGroupId, setTargetGroupId] = useState("");

  // Offer only active Groups of the same Club, other than the current one
  // (people-api.md §15.3); the backend re-checks all of this.
  const targets = (groupsQuery.data ?? []).filter(
    (group) => group.club_id === clubId && group.id !== membership.group_id,
  );
  const targetName = targets.find((group) => group.id === targetGroupId)?.name;

  return (
    <Dialog
      open
      title="Переместить в другую группу"
      description={`${who} будет переведён(а) в выбранную группу. Текущее участие завершится и сохранится в истории.`}
      onClose={onClose}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={transfer.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            disabled={!targetGroupId || transfer.isPending}
            onClick={() =>
              transfer.mutate(
                { membershipId: membership.id, sourceGroupId: membership.group_id, targetGroupId },
                {
                  onSuccess: () => {
                    notify("success", `${who}: перемещён(а) в группу «${targetName ?? ""}»`);
                    onClose();
                  },
                  onError: (error) => notify("error", error.message),
                },
              )
            }
          >
            Переместить
          </Button>
        </>
      }
    >
      {groupsQuery.isLoading ? <Loading label="Загружаем группы…" /> : null}
      {groupsQuery.isSuccess && targets.length === 0 ? (
        <p>Нет других активных групп этого клуба.</p>
      ) : null}
      {groupsQuery.isSuccess && targets.length > 0 ? (
        <FilterSelect
          label="Целевая группа"
          value={targetGroupId}
          options={[
            { value: "", label: "Выберите группу" },
            ...targets.map((group) => ({ value: group.id, label: group.name })),
          ]}
          onChange={setTargetGroupId}
        />
      ) : null}
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
    if (isAccessDenied(scheduleQuery.error)) {
      return (
        <ErrorState
          illustration="403"
          title="Нет доступа"
          description="Расписание этой группы вам недоступно."
        />
      );
    }
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
          <span className={styles.scheduleEntry}>
            <span className={styles.scheduleWhen}>{formatScheduleRange(item.start_at, item.end_at)}</span>
            <span>{item.title}</span>
          </span>
          <StatusBadge
            status={eventStatusIcon(item.status as EventStatus)}
            label={eventStatusLabel(item.status as EventStatus)}
          />
        </li>
      ))}
    </ul>
  );
}
