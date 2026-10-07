import type { FilterOption } from "../components/ui/FilterSelect";
import type { ApiError } from "../api/client";
import type { ExportContext, ExportField, ExportFormat } from "../api/exports";
import { membershipStatusLabel, type MembershipStatus } from "./statusMapping";

/**
 * Shared presentation vocabulary of the Participant Export workflows
 * (docs/04-ux/import-export-ui.md §3.2, §3.3; docs/05-api/
 * participant-export-api.md): the «Экспорт участников» master and the
 * «Участники мероприятий» report run the very same backend dataset, so
 * they share these context/format labels and the rule for which filter
 * keys a context sends. Nothing here selects, filters or authorizes
 * participants — the backend re-validates every combination.
 */

export const CONTEXT_OPTIONS: readonly { value: ExportContext; title: string; description: string }[] = [
  {
    value: "club",
    title: "Все участники",
    description: "Участники клуба с выбранным статусом членства.",
  },
  { value: "group", title: "Участники группы", description: "Состав выбранной группы." },
  { value: "event", title: "Участники события", description: "Участники выбранного события." },
  {
    value: "group_event",
    title: "Группа на событии",
    description: "Участники выбранной группы, относящиеся к выбранному событию.",
  },
];

export const FORMAT_OPTIONS: readonly { value: ExportFormat; title: string; description: string }[] = [
  { value: "xlsx", title: "XLSX", description: "Редактируемая таблица Excel." },
  { value: "pdf", title: "PDF", description: "Готовый к рассылке и печати документ." },
  { value: "print", title: "Печать", description: "Открыть системный диалог печати." },
];

/** Contexts carrying a Group / an Event target (export API §3). Used only
 * to show the matching filter controls and to send only applicable keys —
 * the backend re-validates every combination. */
export const GROUP_CONTEXTS: ReadonlySet<ExportContext> = new Set(["group", "group_event"]);
export const EVENT_CONTEXTS: ReadonlySet<ExportContext> = new Set(["event", "group_event"]);

const CLUB_MEMBERSHIP_STATUSES: readonly MembershipStatus[] = [
  "active",
  "pending",
  "suspended",
  "inactive",
  "archived",
];

const GROUP_MEMBERSHIP_STATUS_OPTIONS: FilterOption[] = [
  { value: "active", label: "Активные" },
  { value: "ended", label: "Завершённые" },
];

/** "No participation filter" — the request then omits
 * `participation_status` and the backend returns every status. Not a
 * business value; every real status comes from
 * `GET /memberships/exports/filters`. */
export const ANY_PARTICIPATION_STATUS: FilterOption = { value: "", label: "Любой статус" };

export function membershipStatusOptions(context: ExportContext): FilterOption[] {
  return GROUP_CONTEXTS.has(context)
    ? GROUP_MEMBERSHIP_STATUS_OPTIONS
    : CLUB_MEMBERSHIP_STATUSES.map((status) => ({
        value: status,
        label: membershipStatusLabel(status),
      }));
}

export function isExportContext(value: string | null): value is ExportContext {
  return CONTEXT_OPTIONS.some((option) => option.value === value);
}

export function exportErrorMessage(error: ApiError): string {
  if (error.status === 403) return "У вас нет прав на этот экспорт.";
  if (error.status === 404) return "Выбранная группа или событие не найдены.";
  return error.message;
}

/** The dataset selection both workflows send — only the keys applicable
 * to `context` (participant-export-api.md §3-§4). */
export type ExportSelectionState = {
  context: ExportContext;
  groupId: string;
  eventId: string;
  membershipStatus: string;
  participationStatus: string;
};

export function selectionRequest(state: ExportSelectionState, fields: string[]) {
  const needsGroup = GROUP_CONTEXTS.has(state.context);
  const needsEvent = EVENT_CONTEXTS.has(state.context);
  return {
    context: state.context,
    fields,
    membership_status: state.membershipStatus,
    ...(needsGroup ? { group_id: state.groupId } : {}),
    ...(needsEvent ? { event_id: state.eventId } : {}),
    ...(needsEvent && state.participationStatus
      ? { participation_status: state.participationStatus }
      : {}),
  };
}

/** `selected` field codes in the backend allowlist's own order, keeping
 * only those the backend offers for the context. */
export function orderedFieldCodes(available: readonly ExportField[], selected: readonly string[]): string[] {
  return available.map((field) => field.field_code).filter((code) => selected.includes(code));
}
