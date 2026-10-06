import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { SearchInput } from "../components/ui/SearchInput";
import { FilterSelect } from "../components/ui/FilterSelect";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Dialog } from "../components/ui/Dialog";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { ObjectListItem } from "../components/ui/ObjectListItem";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser, currentClubId, type RoleAssignmentSummary } from "../api/auth";
import { useArchiveGroup, useCreateGroup, useGroups, type Group } from "../api/groups";
import { groupStatusIcon, groupStatusLabel, type GroupStatus } from "../domain/statusMapping";
import { hasAdministratorRole } from "../shell/navigation";
import styles from "./GroupsPage.module.css";

const STATUS_OPTIONS = [
  { value: "", label: "Все статусы" },
  { value: "active", label: "Активные" },
  { value: "archived", label: "Архивные" },
];

/** Location state GroupDetailPage reads to know it was reached through the
 * Member single-group shortcut below. */
export type GroupsShortcutState = { groupsShortcut: true };

/** Issue #282 (PO decision 2026-10-06): the Member single-group shortcut
 * applies to any user holding the `member` role — additional roles are a
 * UNION (role-permission-scope-matrix.md §12) and do not cancel it — except
 * an Administrator, who keeps the normal Groups list. UI navigation only:
 * which Groups are visible is decided by the backend (`GET /groups`). */
function usesMemberGroupsLanding(roleAssignments: readonly RoleAssignmentSummary[]): boolean {
  const roleCodes = new Set(roleAssignments.map(({ role_code }) => role_code));
  return roleCodes.has("member") && !roleCodes.has("admin");
}

export function GroupsPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<Group | null>(null);

  const meQuery = useCurrentUser();
  const clubId = currentClubId(meQuery.data);
  const roleAssignments = meQuery.data?.role_assignments ?? [];
  // group.manage is admin-only in the current MVP (people-api.md §14,
  // role-permission-scope-matrix.md §6) — same UX-only convention as
  // GroupDetailPage; the backend remains authoritative.
  const canManageGroups = hasAdministratorRole(roleAssignments);
  const groupsQuery = useGroups(
    statusFilter ? { status: statusFilter as GroupStatus } : {},
  );

  const visibleGroups = useMemo(() => {
    const items = groupsQuery.data?.items ?? [];
    if (!search.trim()) return items;
    const needle = search.trim().toLowerCase();
    return items.filter((group) => group.name.toLowerCase().includes(needle));
  }, [groupsQuery.data, search]);

  // Issue #282 single-group shortcut — decided only once both `/auth/me`
  // and the authorized, unfiltered `/groups` collection have resolved.
  const memberLanding = meQuery.isSuccess && usesMemberGroupsLanding(roleAssignments);
  const onlyGroup =
    memberLanding &&
    !statusFilter &&
    !search &&
    groupsQuery.isSuccess &&
    groupsQuery.data.pagination.total === 1 &&
    groupsQuery.data.items.length === 1 &&
    groupsQuery.data.items[0].status === "active"
      ? groupsQuery.data.items[0]
      : null;

  if (onlyGroup) {
    // `replace`: the list entry is never kept in history, so Back from the
    // Group goes to wherever the user came from — no redirect loop.
    const state: GroupsShortcutState = { groupsShortcut: true };
    return <Navigate to={`/groups/${onlyGroup.id}`} replace state={state} />;
  }

  // Until the shortcut can be decided, render neither the list nor its
  // toolbar — a single-group Member must never see the intermediate list.
  if (meQuery.isPending || (memberLanding && groupsQuery.isPending)) {
    return <Loading label="Загружаем группы…" />;
  }

  return (
    <div>
      <PageHeader
        title="Группы"
        description="Учебные и туристские группы клуба."
        actions={
          canManageGroups ? (
            <Button
              variant="primary"
              icon="action.add"
              disabled={!clubId}
              title={clubId ? undefined : "Недоступно без привязки к клубу"}
              onClick={() => setCreateOpen(true)}
            >
              Создать группу
            </Button>
          ) : undefined
        }
      />

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <SearchInput label="Поиск по названию" value={search} onChange={setSearch} placeholder="Например, «Ориентирование»" />
        </div>
        <FilterSelect
          label="Статус"
          value={statusFilter}
          options={STATUS_OPTIONS}
          onChange={setStatusFilter}
        />
      </div>

      {groupsQuery.isLoading ? <Loading label="Загружаем группы…" /> : null}

      {groupsQuery.isError ? (
        <ErrorState
          illustration={groupsQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить группы"
          description={groupsQuery.error.message}
        />
      ) : null}

      {groupsQuery.isSuccess && visibleGroups.length === 0 ? (
        <EmptyState
          illustration={search ? "no-results" : "empty-groups"}
          title={search ? "Ничего не найдено" : "Пока нет ни одной группы"}
          description={
            search
              ? "Попробуйте изменить запрос или сбросить фильтр."
              : canManageGroups
                ? "Создайте первую группу, чтобы начать работу."
                : "Здесь появятся доступные вам группы."
          }
        />
      ) : null}

      {groupsQuery.isSuccess && visibleGroups.length > 0 ? (
        <ul className={styles.list}>
          {visibleGroups.map((group) => (
            <li key={group.id}>
              <ObjectListItem
                title={group.name}
                to={`/groups/${group.id}`}
                description={group.description}
                status={
                  <StatusBadge
                    status={groupStatusIcon(group.status)}
                    label={groupStatusLabel(group.status)}
                  />
                }
                actions={
                  canManageGroups && group.status === "active" ? (
                    <Button variant="secondary" onClick={() => setArchiveTarget(group)}>
                      Архивировать
                    </Button>
                  ) : undefined
                }
              />
            </li>
          ))}
        </ul>
      ) : null}

      <CreateGroupDialog
        open={createOpen}
        clubId={clubId}
        onClose={() => setCreateOpen(false)}
      />
      <ArchiveGroupDialog group={archiveTarget} onClose={() => setArchiveTarget(null)} />
    </div>
  );
}

function CreateGroupDialog({
  open,
  clubId,
  onClose,
}: {
  open: boolean;
  clubId: string | null;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [validFrom, setValidFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const createGroup = useCreateGroup();
  const notify = useNotify();

  function handleClose() {
    setName("");
    setDescription("");
    onClose();
  }

  function handleSubmit() {
    if (!clubId || !name.trim()) return;
    createGroup.mutate(
      {
        club_id: clubId,
        name: name.trim(),
        description: description.trim() || undefined,
        valid_from: new Date(validFrom).toISOString(),
      },
      {
        onSuccess: () => {
          notify("success", `Группа «${name.trim()}» создана`);
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Новая группа"
      description="Минимальные сведения, необходимые для создания группы."
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={createGroup.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!name.trim() || createGroup.isPending}
          >
            Создать
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} required />
        <Input
          label="Описание"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <Input
          label="Дата начала"
          type="date"
          value={validFrom}
          onChange={(event) => setValidFrom(event.target.value)}
        />
      </div>
    </Dialog>
  );
}

function ArchiveGroupDialog({ group, onClose }: { group: Group | null; onClose: () => void }) {
  const archiveGroup = useArchiveGroup();
  const notify = useNotify();

  return (
    <ConfirmDialog
      open={Boolean(group)}
      title="Архивировать группу?"
      description={
        group ? `«${group.name}» перестанет отображаться в активном списке.` : undefined
      }
      confirmLabel="Архивировать"
      destructive
      pending={archiveGroup.isPending}
      onCancel={onClose}
      onConfirm={() => {
        if (!group) return;
        archiveGroup.mutate(group.id, {
          onSuccess: () => {
            notify("success", `Группа «${group.name}» отправлена в архив`);
            onClose();
          },
          onError: (error) => notify("error", error.message),
        });
      }}
    />
  );
}
