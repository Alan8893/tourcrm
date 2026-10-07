import { useState } from "react";

import { Button } from "../components/ui/Button";
import { Loading } from "../components/ui/Loading";
import { FilterSelect } from "../components/ui/FilterSelect";
import { SearchInput } from "../components/ui/SearchInput";
import type { ExportContext, ExportFormat, useExportFilters } from "../api/exports";
import { useGroups } from "../api/groups";
import { useEvent, useEventSearch } from "../api/events";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  ANY_PARTICIPATION_STATUS,
  CONTEXT_OPTIONS,
  EVENT_CONTEXTS,
  FORMAT_OPTIONS,
  GROUP_CONTEXTS,
  membershipStatusOptions,
} from "../domain/participantExport";
import styles from "./ImportExport.module.css";

/**
 * Wizard steps shared by «Отчёты → Экспорт» (ExportPage) and «Отчёты →
 * Участники мероприятий» (EventParticipantsReportPage): both build the
 * one canonical Participant Export dataset, so the context, filter and
 * format choices are the same controls. They only collect the
 * administrator's choices — the backend resolves, filters and authorizes
 * the dataset.
 */

export type SelectedEvent = { id: string; title: string; start_at: string };

function formatEventDate(value: string): string {
  return new Date(value).toLocaleDateString("ru-RU");
}

// --- Context -------------------------------------------------------------------

export function ContextStep({
  context,
  onChange,
}: {
  context: ExportContext | null;
  onChange: (context: ExportContext) => void;
}) {
  return (
    <fieldset className={styles.optionList}>
      <legend className={styles.legend}>Что выгружаем?</legend>
      {CONTEXT_OPTIONS.map((option) => (
        <label key={option.value} className={styles.option}>
          <input
            type="radio"
            name="export-context"
            value={option.value}
            checked={context === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className={styles.optionText}>
            <span className={styles.optionTitle}>{option.title}</span>
            <span className={styles.optionDescription}>{option.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

// --- Filters -------------------------------------------------------------------

export function FiltersStep({
  context,
  groupId,
  onGroupChange,
  event,
  eventId,
  onEventChange,
  membershipStatus,
  onMembershipStatusChange,
  participationStatus,
  onParticipationStatusChange,
  filtersQuery,
}: {
  context: ExportContext;
  groupId: string;
  onGroupChange: (groupId: string) => void;
  event: SelectedEvent | null;
  eventId: string;
  onEventChange: (event: SelectedEvent | null) => void;
  membershipStatus: string;
  onMembershipStatusChange: (value: string) => void;
  participationStatus: string;
  onParticipationStatusChange: (value: string) => void;
  filtersQuery: ReturnType<typeof useExportFilters>;
}) {
  const inGroupContext = GROUP_CONTEXTS.has(context);
  const inEventContext = EVENT_CONTEXTS.has(context);

  return (
    <div className={styles.panel}>
      <h2 className={styles.sectionTitle}>Фильтры</h2>
      <div className={styles.filters}>
        {inGroupContext ? <GroupFilter groupId={groupId} onChange={onGroupChange} /> : null}
        <FilterSelect
          label={inGroupContext ? "Статус в группе" : "Статус членства в клубе"}
          value={membershipStatus}
          options={membershipStatusOptions(context)}
          onChange={onMembershipStatusChange}
        />
        {inEventContext ? (
          <ParticipationStatusFilter
            filtersQuery={filtersQuery}
            value={participationStatus}
            onChange={onParticipationStatusChange}
          />
        ) : null}
      </div>
      {inEventContext ? (
        <EventFilter event={event} eventId={eventId} onChange={onEventChange} />
      ) : null}
    </div>
  );
}

function ParticipationStatusFilter({
  filtersQuery,
  value,
  onChange,
}: {
  filtersQuery: ReturnType<typeof useExportFilters>;
  value: string;
  onChange: (value: string) => void;
}) {
  if (filtersQuery.isLoading) return <Loading label="Загружаем статусы участия…" />;
  if (filtersQuery.isError) {
    return (
      <div className={`${styles.notice} ${styles.noticeError}`} role="alert">
        <p className={styles.muted}>
          Не удалось загрузить статусы участия: {filtersQuery.error.message}. Продолжить экспорт
          по событию можно после повторной загрузки.
        </p>
        <Button variant="secondary" onClick={() => void filtersQuery.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }
  if (!filtersQuery.data) return null;
  return (
    <FilterSelect
      label="Статус участия в событии"
      value={value}
      options={[ANY_PARTICIPATION_STATUS, ...filtersQuery.data.participation_status]}
      onChange={onChange}
    />
  );
}

function GroupFilter({ groupId, onChange }: { groupId: string; onChange: (id: string) => void }) {
  const groupsQuery = useGroups();

  if (groupsQuery.isLoading) return <Loading label="Загружаем группы…" />;
  if (groupsQuery.isError) {
    return (
      <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
        Не удалось загрузить группы: {groupsQuery.error.message}
      </p>
    );
  }
  const groups = groupsQuery.data?.items ?? [];
  if (groups.length === 0) {
    return <p className={styles.muted}>В клубе пока нет групп.</p>;
  }
  return (
    <FilterSelect
      label="Группа"
      value={groupId}
      options={[
        { value: "", label: "Выберите группу" },
        ...groups.map((group) => ({
          value: group.id,
          label: group.status === "archived" ? `${group.name} (архив)` : group.name,
        })),
      ]}
      onChange={onChange}
    />
  );
}

function EventFilter({
  event,
  eventId,
  onChange,
}: {
  event: SelectedEvent | null;
  eventId: string;
  onChange: (event: SelectedEvent | null) => void;
}) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const eventsQuery = useEventSearch(debouncedSearch);
  // A contextual entry point passes only `event_id`; its title is read
  // from the existing Event detail endpoint for display.
  const preselectedQuery = useEvent(eventId && !event ? eventId : undefined);
  const selectedTitle = event?.title ?? preselectedQuery.data?.title;

  return (
    <div className={styles.panel}>
      <p className={styles.muted} aria-live="polite">
        Событие:{" "}
        <strong>
          {eventId ? (selectedTitle ?? "загружаем…") : "не выбрано"}
        </strong>
      </p>
      <SearchInput
        label="Найти событие"
        value={search}
        onChange={setSearch}
        placeholder="Название события"
      />
      {eventsQuery.isLoading ? <Loading label="Ищем события…" /> : null}
      {eventsQuery.isError ? (
        <p className={`${styles.notice} ${styles.noticeError}`} role="alert">
          Не удалось загрузить события: {eventsQuery.error.message}
        </p>
      ) : null}
      {eventsQuery.data ? (
        <ul className={styles.pickerList} aria-label="События">
          {eventsQuery.data.items.length === 0 ? (
            <li className={styles.pickerEmpty}>Событий не найдено.</li>
          ) : (
            eventsQuery.data.items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={`${styles.pickerItem} ${item.id === eventId ? styles.pickerItemSelected : ""}`}
                  aria-pressed={item.id === eventId}
                  onClick={() => onChange({ id: item.id, title: item.title, start_at: item.start_at })}
                >
                  {item.title} · {formatEventDate(item.start_at)}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

// --- Format --------------------------------------------------------------------

export function FormatStep({
  format,
  onChange,
}: {
  format: ExportFormat;
  onChange: (format: ExportFormat) => void;
}) {
  return (
    <fieldset className={styles.optionList}>
      <legend className={styles.legend}>Формат</legend>
      {FORMAT_OPTIONS.map((option) => (
        <label key={option.value} className={styles.option}>
          <input
            type="radio"
            name="export-format"
            value={option.value}
            checked={format === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className={styles.optionText}>
            <span className={styles.optionTitle}>{option.title}</span>
            <span className={styles.optionDescription}>{option.description}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
