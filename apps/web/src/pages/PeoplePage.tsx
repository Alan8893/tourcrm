import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { SearchInput } from "../components/ui/SearchInput";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Dialog } from "../components/ui/Dialog";
import { Loading } from "../components/ui/Loading";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { ObjectListItem } from "../components/ui/ObjectListItem";
import { Pagination } from "../components/ui/Pagination";
import { useNotify } from "../components/ui/notificationContext";
import { useCurrentUser } from "../api/auth";
import {
  usePersons,
  useCreatePersonWizard,
  personFullName,
  personRoleLabel,
  CANONICAL_PERSON_ROLE_CODES,
  type PersonRoleCode,
} from "../api/people";
import { useGroups } from "../api/groups";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import styles from "./PeoplePage.module.css";

function formatBirthDate(birthDate: string | null): string | null {
  if (!birthDate) return null;
  return `Дата рождения: ${new Date(birthDate).toLocaleDateString("ru-RU")}`;
}

export function PeoplePage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const debouncedSearch = useDebouncedValue(search, 300);

  // A new search term always restarts from page 1 — an existing page
  // number for the previous result set would otherwise silently point
  // past the end of the new, narrower one.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const peopleQuery = usePersons({ page, search: debouncedSearch });
  const meQuery = useCurrentUser();
  // `person.create` is admin-only and unconditional (ADR-0035 §2) — no
  // self/other ambiguity to resolve, unlike most other People
  // authorization checks, so a plain role check is both correct and
  // sufficient here. The backend remains the actual enforcement point;
  // this only decides whether to offer the button at all.
  const canCreatePerson = meQuery.data?.role_assignments.some(
    (assignment) => assignment.role_code === "admin",
  );

  return (
    <div>
      <PageHeader
        title="Люди"
        description="Участники и представители клуба."
        actions={
          canCreatePerson ? (
            <Button variant="primary" icon="action.add" onClick={() => setCreateOpen(true)}>
              Добавить человека
            </Button>
          ) : undefined
        }
      />

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <SearchInput
            label="Поиск по имени или роли"
            value={search}
            onChange={setSearch}
            placeholder="Например, «Иванова» или «Инструктор»"
          />
        </div>
      </div>

      {peopleQuery.isLoading ? <Loading label="Загружаем людей…" /> : null}

      {peopleQuery.isError ? (
        <ErrorState
          illustration={peopleQuery.error.status === 403 ? "403" : "error"}
          title="Не удалось загрузить людей"
          description={peopleQuery.error.message}
        />
      ) : null}

      {peopleQuery.isSuccess && peopleQuery.data.items.length === 0 ? (
        <EmptyState
          illustration={debouncedSearch ? "no-results" : "empty-people"}
          title={debouncedSearch ? "Ничего не найдено" : "Пока нет ни одного человека"}
          description={
            debouncedSearch
              ? "Попробуйте изменить запрос поиска."
              : "Здесь появятся участники клуба."
          }
        />
      ) : null}

      {peopleQuery.isSuccess && peopleQuery.data.items.length > 0 ? (
        <>
          <ul className={styles.list}>
            {peopleQuery.data.items.map((person) => (
              <li key={person.id}>
                <ObjectListItem
                  title={personFullName(person)}
                  to={`/people/${person.id}`}
                  description={formatBirthDate(person.birth_date)}
                  status={
                    person.role_codes.length > 0 ? (
                      <div className={styles.roles}>
                        {person.role_codes.map((roleCode) => (
                          <span key={roleCode} className={styles.roleBadge}>
                            {personRoleLabel(roleCode)}
                          </span>
                        ))}
                      </div>
                    ) : undefined
                  }
                />
              </li>
            ))}
          </ul>
          <Pagination
            page={peopleQuery.data.pagination.page}
            pages={peopleQuery.data.pagination.pages}
            total={peopleQuery.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}

      <PersonCreationWizard open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

// --- Person creation wizard (TH-0116 / GitHub Issue #150) -------------------
//
// Person -> basic data -> initial role -> role-specific contextual setup ->
// complete, as ONE atomic backend operation (useCreatePersonWizard). The
// technical entities behind each step (ClubMembership, User, RoleAssignment,
// GroupMembership/GroupInstructorAssignment/GuardianRelationship) never leak
// into this UI: no ClubMembership/membership_type/club selector anywhere
// here (section 6/14).

type WizardStep = "basics" | "role" | "contextual";

function PersonCreationWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState<WizardStep>("basics");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [address, setAddress] = useState("");
  const [roleCode, setRoleCode] = useState<PersonRoleCode | null>(null);
  const [selectedGroupIds, setSelectedGroupIds] = useState<string[]>([]);
  const [selectedChildIds, setSelectedChildIds] = useState<string[]>([]);

  const createWizard = useCreatePersonWizard();
  const notify = useNotify();
  const navigate = useNavigate();

  function reset() {
    setStep("basics");
    setFirstName("");
    setLastName("");
    setMiddleName("");
    setPhone("");
    setEmail("");
    setAddress("");
    setRoleCode(null);
    setSelectedGroupIds([]);
    setSelectedChildIds([]);
  }

  function handleClose() {
    reset();
    onClose();
  }

  const basicsValid = Boolean(firstName.trim() && lastName.trim());
  const needsContextualStep =
    roleCode === "instructor" || roleCode === "member" || roleCode === "guardian";
  const contextualValid =
    roleCode === "member"
      ? selectedGroupIds.length > 0
      : roleCode === "guardian"
        ? selectedChildIds.length > 0
        : true;

  function handleSubmit() {
    if (!roleCode) return;
    createWizard.mutate(
      {
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        middle_name: middleName.trim() || undefined,
        phone: phone.trim() || undefined,
        email: email.trim() || undefined,
        address: address.trim() || undefined,
        role_code: roleCode,
        group_ids: selectedGroupIds,
        child_person_ids: selectedChildIds,
      },
      {
        onSuccess: (result) => {
          notify("success", `Человек «${lastName.trim()} ${firstName.trim()}» добавлен`);
          const issuedCredential = result.temporary_credential;
          reset();
          onClose();
          navigate(`/people/${result.person.id}`, {
            state: issuedCredential ? { issuedCredential } : undefined,
          });
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  function handleNext() {
    if (step === "basics") {
      if (basicsValid) setStep("role");
      return;
    }
    if (step === "role") {
      if (!roleCode) return;
      if (needsContextualStep) {
        setStep("contextual");
      } else {
        handleSubmit();
      }
      return;
    }
    if (step === "contextual" && contextualValid) {
      handleSubmit();
    }
  }

  function handleBack() {
    if (step === "role") setStep("basics");
    else if (step === "contextual") setStep("role");
  }

  const isLastStep = step === "role" ? !needsContextualStep : step === "contextual";
  const nextDisabled =
    createWizard.isPending ||
    (step === "basics" && !basicsValid) ||
    (step === "role" && !roleCode) ||
    (step === "contextual" && !contextualValid);

  const stepTitles: Record<WizardStep, string> = {
    basics: "Новый человек — основные данные",
    role: "Новый человек — роль",
    contextual:
      roleCode === "instructor"
        ? "Новый человек — группы"
        : roleCode === "member"
          ? "Новый человек — группа"
          : "Новый человек — дети",
  };

  return (
    <Dialog
      open={open}
      title={stepTitles[step]}
      onClose={handleClose}
      actions={
        <>
          {step !== "basics" ? (
            <Button variant="secondary" onClick={handleBack} disabled={createWizard.isPending}>
              Назад
            </Button>
          ) : (
            <Button variant="secondary" onClick={handleClose} disabled={createWizard.isPending}>
              Отмена
            </Button>
          )}
          <Button variant="primary" onClick={handleNext} disabled={nextDisabled}>
            {createWizard.isPending ? "Создание…" : isLastStep ? "Создать" : "Далее"}
          </Button>
        </>
      }
    >
      {step === "basics" ? (
        <div className={styles.form}>
          <Input
            label="Фамилия"
            value={lastName}
            onChange={(event) => setLastName(event.target.value)}
            required
          />
          <Input
            label="Имя"
            value={firstName}
            onChange={(event) => setFirstName(event.target.value)}
            required
          />
          <Input
            label="Отчество"
            value={middleName}
            onChange={(event) => setMiddleName(event.target.value)}
          />
          <Input
            label="Email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Input label="Телефон" value={phone} onChange={(event) => setPhone(event.target.value)} />
          <Input label="Адрес" value={address} onChange={(event) => setAddress(event.target.value)} />
        </div>
      ) : null}

      {step === "role" ? (
        <div className={styles.roleOptions}>
          {CANONICAL_PERSON_ROLE_CODES.map((code) => (
            <Button
              key={code}
              variant={roleCode === code ? "primary" : "secondary"}
              onClick={() => setRoleCode(code)}
            >
              {personRoleLabel(code)}
            </Button>
          ))}
        </div>
      ) : null}

      {step === "contextual" && (roleCode === "instructor" || roleCode === "member") ? (
        <WizardGroupPicker selectedIds={selectedGroupIds} onChange={setSelectedGroupIds} />
      ) : null}

      {step === "contextual" && roleCode === "guardian" ? (
        <WizardChildPicker
          lastName={lastName}
          selectedIds={selectedChildIds}
          onChange={setSelectedChildIds}
        />
      ) : null}
    </Dialog>
  );
}

function WizardGroupPicker({
  selectedIds,
  onChange,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  const groupsQuery = useGroups({ status: "active" });

  const filteredGroups = (groupsQuery.data?.items ?? []).filter((group) =>
    group.name.toLowerCase().includes(search.trim().toLowerCase()),
  );

  function toggle(groupId: string) {
    onChange(
      selectedIds.includes(groupId)
        ? selectedIds.filter((id) => id !== groupId)
        : [...selectedIds, groupId],
    );
  }

  return (
    <div className={styles.form}>
      <SearchInput
        label="Поиск группы"
        value={search}
        onChange={setSearch}
        placeholder="Например, «Юниоры»"
      />
      {groupsQuery.isLoading ? <Loading label="Загружаем группы…" /> : null}
      {groupsQuery.isError ? (
        <ErrorState
          illustration="error"
          title="Не удалось загрузить группы"
          description={groupsQuery.error.message}
        />
      ) : null}
      {groupsQuery.isSuccess ? (
        <ul className={styles.pickerList}>
          {filteredGroups.map((group) => (
            <li key={group.id}>
              <label className={styles.pickerItemRow}>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(group.id)}
                  onChange={() => toggle(group.id)}
                />
                {group.name}
              </label>
            </li>
          ))}
          {filteredGroups.length === 0 ? (
            <li className={styles.pickerEmpty}>Ничего не найдено</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function WizardChildPicker({
  lastName,
  selectedIds,
  onChange,
}: {
  lastName: string;
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  // Section 11: the search field may be pre-filled with the guardian's own
  // surname as a search convenience only — it never pre-selects a child.
  const [search, setSearch] = useState(lastName);
  const debouncedSearch = useDebouncedValue(search, 300);
  const searchQuery = usePersons({ page: 1, search: debouncedSearch });

  function toggle(personId: string) {
    onChange(
      selectedIds.includes(personId)
        ? selectedIds.filter((id) => id !== personId)
        : [...selectedIds, personId],
    );
  }

  return (
    <div className={styles.form}>
      <SearchInput
        label="Поиск ребёнка"
        value={search}
        onChange={setSearch}
        placeholder="Например, «Иванов»"
      />
      {searchQuery.isSuccess ? (
        <ul className={styles.pickerList}>
          {searchQuery.data.items.map((candidate) => (
            <li key={candidate.id}>
              <label className={styles.pickerItemRow}>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(candidate.id)}
                  onChange={() => toggle(candidate.id)}
                />
                {personFullName(candidate)}
              </label>
            </li>
          ))}
          {searchQuery.data.items.length === 0 ? (
            <li className={styles.pickerEmpty}>Ничего не найдено</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
