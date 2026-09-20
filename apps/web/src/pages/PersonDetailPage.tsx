import { useState } from "react";
import { useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { FilterSelect } from "../components/ui/FilterSelect";
import { SearchInput } from "../components/ui/SearchInput";
import { Dialog } from "../components/ui/Dialog";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser, currentClubId } from "../api/auth";
import {
  personFullName,
  usePerson,
  usePersons,
  usePersonGuardianRelationships,
  usePersonMemberships,
  useCreateGuardianRelationship,
  useCreateMembership,
  useTerminateGuardianRelationship,
  useTransitionMembershipStatus,
  useUpdateGuardianRelationship,
  useUpdateMembershipType,
  useUpdatePerson,
  CANONICAL_MEMBERSHIP_STATUSES,
  MEMBERSHIP_NEXT_STATUSES,
  type GuardianRelationship,
  type Membership,
  type Person,
  type PersonFields,
} from "../api/people";
import {
  membershipStatusIcon,
  membershipStatusLabel,
  guardianRelationshipStatusIcon,
  guardianRelationshipStatusLabel,
  type MembershipStatus,
} from "../domain/statusMapping";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import styles from "./PersonDetailPage.module.css";

export function PersonDetailPage() {
  const { personId } = useParams<{ personId: string }>();
  const personQuery = usePerson(personId);
  const [activeTab, setActiveTab] = useState("overview");
  const [editOpen, setEditOpen] = useState(false);
  const meQuery = useCurrentUser();
  // Person create/update, ClubMembership lifecycle and GuardianRelationship
  // management are all admin-only and unconditional (ADR-0035 §2/§7/§8) —
  // no self/other ambiguity to resolve for any of these actions, so a
  // plain role check is both correct and sufficient. The backend remains
  // the actual enforcement point regardless; this only decides whether to
  // offer the controls at all.
  const isAdmin = meQuery.data?.role_assignments.some((a) => a.role_code === "admin") ?? false;

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
      <PageHeader
        title={personFullName(person)}
        back={{ to: "/people", label: "Все люди" }}
        actions={
          <Button variant="secondary" icon="action.edit" onClick={() => setEditOpen(true)}>
            Редактировать
          </Button>
        }
      />

      <Tabs
        label="Разделы профиля"
        activeId={activeTab}
        onChange={setActiveTab}
        items={[
          {
            id: "overview",
            label: "Обзор",
            content: <OverviewTab person={person} />,
          },
          {
            id: "memberships",
            label: "Членство",
            content: <MembershipsTab personId={person.id} isAdmin={isAdmin} />,
          },
          {
            id: "guardians",
            label: "Представители",
            content: <GuardiansTab personId={person.id} isAdmin={isAdmin} />,
          },
        ]}
      />

      <EditPersonDialog
        key={`${person.id}-${person.updated_at}`}
        open={editOpen}
        onClose={() => setEditOpen(false)}
        person={person}
        isAdmin={isAdmin}
      />
    </div>
  );
}

function OverviewTab({ person }: { person: Person }) {
  return (
    <div className={styles.metaRow}>
      <div>
        <span className={styles.metaLabel}>Дата рождения</span>
        {person.birth_date ? new Date(person.birth_date).toLocaleDateString("ru-RU") : "Не указана"}
      </div>
      <div>
        <span className={styles.metaLabel}>Телефон</span>
        {person.phone || "Не указан"}
      </div>
      <div>
        <span className={styles.metaLabel}>Email</span>
        {person.email || "Не указан"}
      </div>
      <div>
        <span className={styles.metaLabel}>Адрес</span>
        {person.address || "Не указан"}
      </div>
    </div>
  );
}

function EditPersonDialog({
  open,
  onClose,
  person,
  isAdmin,
}: {
  open: boolean;
  onClose: () => void;
  person: Person;
  isAdmin: boolean;
}) {
  const [firstName, setFirstName] = useState(person.first_name);
  const [lastName, setLastName] = useState(person.last_name);
  const [middleName, setMiddleName] = useState(person.middle_name ?? "");
  const [phone, setPhone] = useState(person.phone ?? "");
  const [email, setEmail] = useState(person.email ?? "");
  const [address, setAddress] = useState(person.address ?? "");
  const [photoFileId, setPhotoFileId] = useState(person.photo_file_id ?? "");
  const [birthDate, setBirthDate] = useState(person.birth_date ?? "");
  const updatePerson = useUpdatePerson();
  const notify = useNotify();

  function handleSubmit() {
    if (!firstName.trim() || !lastName.trim()) return;

    // PATCH semantics: only send fields that actually changed.
    const fields: PersonFields = {};
    if (firstName.trim() !== person.first_name) fields.first_name = firstName.trim();
    if (lastName.trim() !== person.last_name) fields.last_name = lastName.trim();
    if ((middleName.trim() || null) !== person.middle_name) {
      fields.middle_name = middleName.trim() || null;
    }
    if ((phone.trim() || null) !== person.phone) fields.phone = phone.trim() || null;
    if ((email.trim() || null) !== person.email) fields.email = email.trim() || null;
    if ((address.trim() || null) !== person.address) fields.address = address.trim() || null;
    if ((photoFileId.trim() || null) !== person.photo_file_id) {
      fields.photo_file_id = photoFileId.trim() || null;
    }
    // birth_date is never offered to a non-admin, so it can never appear
    // in the diff for one — matches "non-admin roles must not be offered
    // birth-date editing" beyond merely disabling the field.
    if (isAdmin && (birthDate || null) !== person.birth_date) {
      fields.birth_date = birthDate || null;
    }

    if (Object.keys(fields).length === 0) {
      onClose();
      return;
    }

    updatePerson.mutate(
      { personId: person.id, fields },
      {
        onSuccess: () => {
          notify("success", "Данные обновлены");
          onClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Редактировать данные"
      onClose={onClose}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={updatePerson.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!firstName.trim() || !lastName.trim() || updatePerson.isPending}
          >
            Сохранить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input label="Фамилия" value={lastName} onChange={(e) => setLastName(e.target.value)} required />
        <Input label="Имя" value={firstName} onChange={(e) => setFirstName(e.target.value)} required />
        <Input label="Отчество" value={middleName} onChange={(e) => setMiddleName(e.target.value)} />
        <Input label="Телефон" value={phone} onChange={(e) => setPhone(e.target.value)} />
        <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Input label="Адрес" value={address} onChange={(e) => setAddress(e.target.value)} />
        <Input
          label="ID файла фото (необязательно)"
          value={photoFileId}
          onChange={(e) => setPhotoFileId(e.target.value)}
          hint="Ссылка на уже загруженный файл — загрузка фото в этой версии не реализована."
        />
        {isAdmin ? (
          <Input
            label="Дата рождения"
            type="date"
            value={birthDate}
            onChange={(e) => setBirthDate(e.target.value)}
          />
        ) : null}
      </div>
    </Dialog>
  );
}

// --- Membership tab ---------------------------------------------------

function MembershipsTab({ personId, isAdmin }: { personId: string; isAdmin: boolean }) {
  const membershipsQuery = usePersonMemberships(personId);
  const [createOpen, setCreateOpen] = useState(false);
  const [typeTarget, setTypeTarget] = useState<Membership | null>(null);
  const [statusTarget, setStatusTarget] = useState<Membership | null>(null);

  return (
    <div>
      {isAdmin ? (
        <div className={styles.tabActions}>
          <Button variant="secondary" icon="action.add" onClick={() => setCreateOpen(true)}>
            Добавить членство
          </Button>
        </div>
      ) : null}

      {membershipsQuery.isLoading ? <Loading label="Загружаем членство…" /> : null}
      {membershipsQuery.isError ? (
        <ErrorState
          illustration="error"
          title="Не удалось загрузить членство"
          description={membershipsQuery.error.message}
        />
      ) : null}
      {membershipsQuery.isSuccess && membershipsQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="empty-people"
          title="Нет данных о членстве"
          description="У этого человека пока нет периодов членства в клубе."
        />
      ) : null}
      {membershipsQuery.isSuccess && membershipsQuery.data.items.length > 0 ? (
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
              <div className={styles.rowActions}>
                <StatusBadge
                  status={membershipStatusIcon(membership.status)}
                  label={membershipStatusLabel(membership.status)}
                />
                {isAdmin ? (
                  <>
                    <Button variant="secondary" onClick={() => setTypeTarget(membership)}>
                      Изменить тип
                    </Button>
                    {MEMBERSHIP_NEXT_STATUSES[membership.status].length > 0 ? (
                      <Button variant="secondary" onClick={() => setStatusTarget(membership)}>
                        Изменить статус
                      </Button>
                    ) : null}
                  </>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <CreateMembershipDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        personId={personId}
      />
      <ChangeMembershipTypeDialog
        key={`type-${typeTarget?.id ?? "none"}`}
        membership={typeTarget}
        personId={personId}
        onClose={() => setTypeTarget(null)}
      />
      <ChangeMembershipStatusDialog
        key={`status-${statusTarget?.id ?? "none"}`}
        membership={statusTarget}
        personId={personId}
        onClose={() => setStatusTarget(null)}
      />
    </div>
  );
}

function CreateMembershipDialog({
  open,
  onClose,
  personId,
}: {
  open: boolean;
  onClose: () => void;
  personId: string;
}) {
  const meQuery = useCurrentUser();
  const clubId = currentClubId(meQuery.data);
  const [membershipType, setMembershipType] = useState("");
  const [status, setStatus] = useState<MembershipStatus>("active");
  const [joinedAt, setJoinedAt] = useState(() => new Date().toISOString().slice(0, 10));
  const createMembership = useCreateMembership();
  const notify = useNotify();

  function reset() {
    setMembershipType("");
    setStatus("active");
    setJoinedAt(new Date().toISOString().slice(0, 10));
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    if (!clubId || !membershipType.trim()) return;
    createMembership.mutate(
      {
        person_id: personId,
        club_id: clubId,
        membership_type: membershipType.trim(),
        status,
        joined_at: new Date(joinedAt).toISOString(),
      },
      {
        onSuccess: () => {
          notify("success", "Членство добавлено");
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Новое членство"
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={createMembership.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!clubId || !membershipType.trim() || createMembership.isPending}
          >
            Создать
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input
          label="Тип членства"
          value={membershipType}
          onChange={(e) => setMembershipType(e.target.value)}
          placeholder="Например, «участник»"
          required
        />
        <FilterSelect
          label="Статус"
          value={status}
          options={CANONICAL_MEMBERSHIP_STATUSES.map((value) => ({
            value,
            label: membershipStatusLabel(value),
          }))}
          onChange={(value) => setStatus(value as MembershipStatus)}
        />
        <Input
          label="Дата вступления"
          type="date"
          value={joinedAt}
          onChange={(e) => setJoinedAt(e.target.value)}
        />
      </div>
    </Dialog>
  );
}

function ChangeMembershipTypeDialog({
  membership,
  personId,
  onClose,
}: {
  membership: Membership | null;
  personId: string;
  onClose: () => void;
}) {
  const [membershipType, setMembershipType] = useState(membership?.membership_type ?? "");
  const updateType = useUpdateMembershipType();
  const notify = useNotify();

  function handleSubmit() {
    if (!membership || !membershipType.trim()) return;
    updateType.mutate(
      { membershipId: membership.id, personId, membership_type: membershipType.trim() },
      {
        onSuccess: () => {
          notify("success", "Тип членства обновлён");
          onClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={Boolean(membership)}
      title="Изменить тип членства"
      onClose={onClose}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={updateType.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!membershipType.trim() || updateType.isPending}
          >
            Сохранить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input
          label="Тип членства"
          value={membershipType}
          onChange={(e) => setMembershipType(e.target.value)}
          required
        />
      </div>
    </Dialog>
  );
}

function ChangeMembershipStatusDialog({
  membership,
  personId,
  onClose,
}: {
  membership: Membership | null;
  personId: string;
  onClose: () => void;
}) {
  const nextStatuses = membership ? MEMBERSHIP_NEXT_STATUSES[membership.status] : [];
  const [status, setStatus] = useState<MembershipStatus>(nextStatuses[0] ?? "active");
  const [reason, setReason] = useState("");
  const transition = useTransitionMembershipStatus();
  const notify = useNotify();

  function handleClose() {
    setReason("");
    onClose();
  }

  function handleSubmit() {
    if (!membership) return;
    transition.mutate(
      { membershipId: membership.id, personId, status, reason: reason.trim() || undefined },
      {
        onSuccess: () => {
          notify("success", "Статус членства обновлён");
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={Boolean(membership)}
      title="Изменить статус членства"
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={transition.isPending}>
            Отмена
          </Button>
          <Button variant="primary" onClick={handleSubmit} disabled={transition.isPending}>
            Сохранить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <FilterSelect
          label="Новый статус"
          value={status}
          options={nextStatuses.map((value) => ({ value, label: membershipStatusLabel(value) }))}
          onChange={(value) => setStatus(value as MembershipStatus)}
        />
        <Input
          label="Причина (необязательно)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
    </Dialog>
  );
}

// --- Guardians tab ------------------------------------------------------

function GuardiansTab({ personId, isAdmin }: { personId: string; isAdmin: boolean }) {
  const guardiansQuery = usePersonGuardianRelationships(personId);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<GuardianRelationship | null>(null);
  const [terminateTarget, setTerminateTarget] = useState<GuardianRelationship | null>(null);

  return (
    <div>
      {isAdmin ? (
        <div className={styles.tabActions}>
          <Button variant="secondary" icon="action.add" onClick={() => setCreateOpen(true)}>
            Добавить представителя
          </Button>
        </div>
      ) : null}

      {guardiansQuery.isLoading ? <Loading label="Загружаем представителей…" /> : null}
      {guardiansQuery.isError ? (
        <ErrorState
          illustration="error"
          title="Не удалось загрузить представителей"
          description={guardiansQuery.error.message}
        />
      ) : null}
      {guardiansQuery.isSuccess && guardiansQuery.data.items.length === 0 ? (
        <EmptyState
          illustration="empty-people"
          title="Законные представители не указаны"
          description="Для этого человека не найдено ни одной связи с представителем."
        />
      ) : null}
      {guardiansQuery.isSuccess && guardiansQuery.data.items.length > 0 ? (
        <ul className={styles.list}>
          {guardiansQuery.data.items.map((relationship) => (
            <GuardianRow
              key={relationship.id}
              relationship={relationship}
              isAdmin={isAdmin}
              onEdit={setEditTarget}
              onTerminate={setTerminateTarget}
            />
          ))}
        </ul>
      ) : null}

      <CreateGuardianRelationshipDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        personId={personId}
      />
      <EditGuardianRelationshipDialog
        key={editTarget?.id ?? "none"}
        relationship={editTarget}
        personId={personId}
        onClose={() => setEditTarget(null)}
      />
      <TerminateGuardianRelationshipDialog
        relationship={terminateTarget}
        personId={personId}
        onClose={() => setTerminateTarget(null)}
      />
    </div>
  );
}

function GuardianRow({
  relationship,
  isAdmin,
  onEdit,
  onTerminate,
}: {
  relationship: GuardianRelationship;
  isAdmin: boolean;
  onEdit: (relationship: GuardianRelationship) => void;
  onTerminate: (relationship: GuardianRelationship) => void;
}) {
  const guardianQuery = usePerson(relationship.guardian_person_id);
  const name = guardianQuery.data ? personFullName(guardianQuery.data) : null;

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <span>{guardianQuery.isLoading ? "Загрузка…" : (name ?? "Представитель недоступен")}</span>
        <span className={styles.rowSecondary}>{relationship.relationship_type}</span>
      </div>
      <div className={styles.rowActions}>
        <StatusBadge
          status={guardianRelationshipStatusIcon(relationship.status)}
          label={guardianRelationshipStatusLabel(relationship.status)}
        />
        {isAdmin ? (
          <>
            <Button variant="secondary" onClick={() => onEdit(relationship)}>
              Изменить тип
            </Button>
            {relationship.status !== "revoked" ? (
              <Button
                variant="destructive"
                icon="action.archive"
                onClick={() => onTerminate(relationship)}
              >
                Прекратить
              </Button>
            ) : null}
          </>
        ) : null}
      </div>
    </li>
  );
}

function CreateGuardianRelationshipDialog({
  open,
  onClose,
  personId,
}: {
  open: boolean;
  onClose: () => void;
  personId: string;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);
  const [relationshipType, setRelationshipType] = useState("");
  const searchQuery = usePersons({ page: 1, search: debouncedSearch });
  const createRelationship = useCreateGuardianRelationship();
  const notify = useNotify();

  function reset() {
    setSearch("");
    setSelected(null);
    setRelationshipType("");
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    if (!selected || !relationshipType.trim()) return;
    createRelationship.mutate(
      { personId, guardian_person_id: selected.id, relationship_type: relationshipType.trim() },
      {
        onSuccess: () => {
          notify("success", "Представитель добавлен");
          handleClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={open}
      title="Добавить представителя"
      onClose={handleClose}
      actions={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={createRelationship.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!selected || !relationshipType.trim() || createRelationship.isPending}
          >
            Добавить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        {selected ? (
          <div className={styles.selectedPerson}>
            <span>{selected.name}</span>
            <Button variant="secondary" onClick={() => setSelected(null)}>
              Изменить выбор
            </Button>
          </div>
        ) : (
          <>
            <SearchInput
              label="Поиск человека"
              value={search}
              onChange={setSearch}
              placeholder="Например, «Иванова»"
            />
            {searchQuery.isSuccess && debouncedSearch ? (
              <ul className={styles.pickerList}>
                {searchQuery.data.items
                  .filter((candidate) => candidate.id !== personId)
                  .map((candidate) => (
                    <li key={candidate.id}>
                      <button
                        type="button"
                        className={styles.pickerItem}
                        onClick={() =>
                          setSelected({ id: candidate.id, name: personFullName(candidate) })
                        }
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
          </>
        )}
        <Input
          label="Тип связи"
          value={relationshipType}
          onChange={(e) => setRelationshipType(e.target.value)}
          placeholder="Например, «родитель»"
          required
        />
      </div>
    </Dialog>
  );
}

function EditGuardianRelationshipDialog({
  relationship,
  personId,
  onClose,
}: {
  relationship: GuardianRelationship | null;
  personId: string;
  onClose: () => void;
}) {
  const [relationshipType, setRelationshipType] = useState(relationship?.relationship_type ?? "");
  const updateRelationship = useUpdateGuardianRelationship();
  const notify = useNotify();

  function handleSubmit() {
    if (!relationship || !relationshipType.trim()) return;
    updateRelationship.mutate(
      { relationshipId: relationship.id, personId, relationship_type: relationshipType.trim() },
      {
        onSuccess: () => {
          notify("success", "Тип связи обновлён");
          onClose();
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <Dialog
      open={Boolean(relationship)}
      title="Изменить тип связи"
      onClose={onClose}
      actions={
        <>
          <Button variant="secondary" onClick={onClose} disabled={updateRelationship.isPending}>
            Отмена
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!relationshipType.trim() || updateRelationship.isPending}
          >
            Сохранить
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Input
          label="Тип связи"
          value={relationshipType}
          onChange={(e) => setRelationshipType(e.target.value)}
          required
        />
      </div>
    </Dialog>
  );
}

function TerminateGuardianRelationshipDialog({
  relationship,
  personId,
  onClose,
}: {
  relationship: GuardianRelationship | null;
  personId: string;
  onClose: () => void;
}) {
  const terminate = useTerminateGuardianRelationship();
  const notify = useNotify();

  return (
    <ConfirmDialog
      open={Boolean(relationship)}
      title="Прекратить связь с представителем?"
      description="Это действие необратимо: связь будет отмечена как отозванная, восстановить её будет нельзя — потребуется создать новую."
      confirmLabel="Прекратить"
      destructive
      pending={terminate.isPending}
      onCancel={onClose}
      onConfirm={() => {
        if (!relationship) return;
        terminate.mutate(
          { relationshipId: relationship.id, personId },
          {
            onSuccess: () => {
              notify("success", "Связь с представителем прекращена");
              onClose();
            },
            onError: (error) => notify("error", error.message),
          },
        );
      }}
    />
  );
}
