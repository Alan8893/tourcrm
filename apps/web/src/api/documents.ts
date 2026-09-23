import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, apiFetchBlob, ApiError, type CollectionResponse } from "./client";
import type { DocumentRequirementResult, DocumentStatus } from "../domain/statusMapping";

/**
 * Participant Document / EventDocumentRequirement / competition document
 * package API client (TH-0117 / Issue #175, ADR-0040). Endpoints and
 * response shapes match the real routers (`app/api/v1/persons.py`'s
 * "Participant Documents" section, `app/api/v1/events.py`'s "Event
 * document requirements"/"competition document package export"
 * sections) and `docs/05-api/people-api.md` §32 / `docs/05-api/
 * events-api.md` §31 — no field or endpoint is invented here.
 *
 * Every derived value this module surfaces (`Document.status`,
 * `EventDocumentRequirementCheckOut.result`, package completeness) comes
 * straight from the backend response and is rendered as-is; this client
 * never recomputes validity/expiration/readiness itself (Issue #175
 * business rule).
 */

export type Document = {
  id: string;
  person_id: string;
  document_group_id: string;
  version_number: number;
  document_type: string;
  status: DocumentStatus;
  issued_at: string | null;
  expires_at: string | null;
  file_id: string;
  uploaded_by: string | null;
  created_at: string;
  updated_at: string;
};

const PERSON_DOCUMENTS_PAGE_SIZE = 50;

/** `GET /api/v1/persons/{person_id}/documents` (people-api.md §32) —
 * `document.read`. Current versions only.
 *
 * KNOWN CONTRACT GAP (Issue #175 "document history/versions"):
 * people-api.md §32 defines no endpoint that lists every historical
 * version of a `document_group_id` — only this current-versions list,
 * and `GET .../documents/{document_id}`, which returns one version
 * (current or historical) but only when its own id is already known to
 * the caller. Neither this client nor the Documents tab UI enumerates or
 * displays historical versions: doing so client-side (e.g. by
 * remembering whichever version a `replace` call happened to supersede
 * during the current browser session) would not be "history", only a
 * fragile, incomplete echo of one session's own actions presented as if
 * it were the complete record — not implemented here. Full version
 * history requires a backend endpoint this contract does not yet expose
 * (e.g. a `document_group_id`-scoped version list). */
export function usePersonDocuments(personId: string | undefined) {
  return useQuery<CollectionResponse<Document>, ApiError>({
    queryKey: ["persons", "documents", personId],
    queryFn: () =>
      apiFetch<CollectionResponse<Document>>(
        `/persons/${personId}/documents?page_size=${PERSON_DOCUMENTS_PAGE_SIZE}`,
      ),
    enabled: Boolean(personId),
  });
}

function invalidatePersonDocuments(queryClient: ReturnType<typeof useQueryClient>, personId: string) {
  void queryClient.invalidateQueries({ queryKey: ["persons", "documents", personId] });
}

export type CreatePersonDocumentInput = {
  personId: string;
  file: File;
  document_type: string;
  issued_at?: string;
  expires_at?: string;
};

function documentFormData(fields: {
  file: File;
  document_type?: string;
  issued_at?: string;
  expires_at?: string;
}): FormData {
  const form = new FormData();
  form.set("file", fields.file);
  if (fields.document_type !== undefined) form.set("document_type", fields.document_type);
  if (fields.issued_at) form.set("issued_at", fields.issued_at);
  if (fields.expires_at) form.set("expires_at", fields.expires_at);
  return form;
}

/** `POST /api/v1/persons/{person_id}/documents` (people-api.md §32) —
 * `document.manage`. Multipart request: the backend's `create_person_
 * document` endpoint takes `file`/`document_type`/`issued_at`/
 * `expires_at` as `File(...)`/`Form(...)` parameters, not a JSON body. */
export function useCreatePersonDocument() {
  const queryClient = useQueryClient();
  return useMutation<Document, ApiError, CreatePersonDocumentInput>({
    mutationFn: ({ personId, ...fields }) =>
      apiFetch<Document>(`/persons/${personId}/documents`, {
        method: "POST",
        body: documentFormData(fields),
      }),
    onSuccess: (_document, { personId }) => invalidatePersonDocuments(queryClient, personId),
  });
}

export type UpdatePersonDocumentDatesInput = {
  personId: string;
  documentId: string;
  fields: { issued_at?: string | null; expires_at?: string | null };
};

/** `PATCH /api/v1/persons/{person_id}/documents/{document_id}`
 * (people-api.md §32) — `document.manage`. Only `issued_at`/`expires_at`
 * are ever sent; the caller is responsible for including only the
 * field(s) that actually changed (PATCH/`exclude_unset` semantics, same
 * convention as `useUpdatePerson`). Never touches the file/version —
 * this is metadata correction in place, distinct from `useReplacePerson
 * Document`. */
export function useUpdatePersonDocumentDates() {
  const queryClient = useQueryClient();
  return useMutation<Document, ApiError, UpdatePersonDocumentDatesInput>({
    mutationFn: ({ personId, documentId, fields }) =>
      apiFetch<Document>(`/persons/${personId}/documents/${documentId}`, {
        method: "PATCH",
        body: JSON.stringify(fields),
      }),
    onSuccess: (_document, { personId }) => invalidatePersonDocuments(queryClient, personId),
  });
}

export type ReplacePersonDocumentInput = {
  personId: string;
  documentId: string;
  file: File;
  issued_at?: string;
  expires_at?: string;
};

/** `POST /api/v1/persons/{person_id}/documents/{document_id}/replace`
 * (people-api.md §32) — `document.manage`. Creates a new version with a
 * new file; `document_type` is never sent (the backend always copies it
 * from the version being replaced). */
export function useReplacePersonDocument() {
  const queryClient = useQueryClient();
  return useMutation<Document, ApiError, ReplacePersonDocumentInput>({
    mutationFn: ({ personId, documentId, ...fields }) =>
      apiFetch<Document>(`/persons/${personId}/documents/${documentId}/replace`, {
        method: "POST",
        body: documentFormData(fields),
      }),
    onSuccess: (_document, { personId }) => invalidatePersonDocuments(queryClient, personId),
  });
}

/** `POST /api/v1/persons/{person_id}/documents/{document_id}/revoke`
 * (people-api.md §32) — `document.manage`. No request body: a fixed
 * `status -> revoked` transition, nothing client-configurable. */
export function useRevokePersonDocument() {
  const queryClient = useQueryClient();
  return useMutation<Document, ApiError, { personId: string; documentId: string }>({
    mutationFn: ({ personId, documentId }) =>
      apiFetch<Document>(`/persons/${personId}/documents/${documentId}/revoke`, { method: "POST" }),
    onSuccess: (_document, { personId }) => invalidatePersonDocuments(queryClient, personId),
  });
}

/** `GET /api/v1/persons/{person_id}/documents/{document_id}/download`
 * (people-api.md §32) — `document.read`. Modeled as a mutation (not a
 * query) because it is an explicit, user-triggered download action, not
 * data the page loads on mount — matching how a click-triggered fetch
 * with its own pending/error state is otherwise expressed in this
 * codebase. Never touches `storage_key`/filesystem details: those never
 * leave the backend (ADR-0040 §3). */
export function useDownloadPersonDocument() {
  return useMutation<
    { blob: Blob; filename: string | null },
    ApiError,
    { personId: string; documentId: string }
  >({
    mutationFn: ({ personId, documentId }) =>
      apiFetchBlob(`/persons/${personId}/documents/${documentId}/download`),
  });
}

// --- Event document requirements (TH-0117.5 / ADR-0040, events-api.md §31) -

export type EventDocumentRequirement = {
  id: string;
  event_id: string;
  document_type: string;
  required: boolean;
};

const EVENT_DOCUMENT_REQUIREMENTS_PAGE_SIZE = 50;

/** `GET /api/v1/events/{event_id}/document-requirements`
 * (events-api.md §31.1) — `event.read` + `document.read`. */
export function useEventDocumentRequirements(eventId: string | undefined) {
  return useQuery<CollectionResponse<EventDocumentRequirement>, ApiError>({
    queryKey: ["events", "document-requirements", eventId],
    queryFn: () =>
      apiFetch<CollectionResponse<EventDocumentRequirement>>(
        `/events/${eventId}/document-requirements?page_size=${EVENT_DOCUMENT_REQUIREMENTS_PAGE_SIZE}`,
      ),
    enabled: Boolean(eventId),
  });
}

function invalidateEventDocumentRequirements(
  queryClient: ReturnType<typeof useQueryClient>,
  eventId: string,
) {
  void queryClient.invalidateQueries({ queryKey: ["events", "document-requirements", eventId] });
}

/** `POST /api/v1/events/{event_id}/document-requirements`
 * (events-api.md §31.1) — `event.manage` + `document.manage`. Rejects
 * with `duplicate_document_requirement` (409) for a repeated
 * `(event_id, document_type)` pair — an ordinary `ApiError` for the
 * caller to surface. */
export function useCreateEventDocumentRequirement() {
  const queryClient = useQueryClient();
  return useMutation<
    EventDocumentRequirement,
    ApiError,
    { eventId: string; document_type: string; required: boolean }
  >({
    mutationFn: ({ eventId, document_type, required }) =>
      apiFetch<EventDocumentRequirement>(`/events/${eventId}/document-requirements`, {
        method: "POST",
        body: JSON.stringify({ document_type, required }),
      }),
    onSuccess: (_requirement, { eventId }) => invalidateEventDocumentRequirements(queryClient, eventId),
  });
}

/** `PATCH /api/v1/events/{event_id}/document-requirements/{requirement_id}`
 * (events-api.md §31.1) — `event.manage` + `document.manage`. Changes
 * only `required`; `document_type` is immutable (delete + create is the
 * only way to change it, matching the backend contract exactly). */
export function useUpdateEventDocumentRequirement() {
  const queryClient = useQueryClient();
  return useMutation<
    EventDocumentRequirement,
    ApiError,
    { eventId: string; requirementId: string; required: boolean }
  >({
    mutationFn: ({ eventId, requirementId, required }) =>
      apiFetch<EventDocumentRequirement>(
        `/events/${eventId}/document-requirements/${requirementId}`,
        { method: "PATCH", body: JSON.stringify({ required }) },
      ),
    onSuccess: (_requirement, { eventId }) => invalidateEventDocumentRequirements(queryClient, eventId),
  });
}

/** `DELETE /api/v1/events/{event_id}/document-requirements/{requirement_id}`
 * (events-api.md §31.1) — `event.manage` + `document.manage`. */
export function useDeleteEventDocumentRequirement() {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, { eventId: string; requirementId: string }>({
    mutationFn: ({ eventId, requirementId }) =>
      apiFetch<void>(`/events/${eventId}/document-requirements/${requirementId}`, {
        method: "DELETE",
      }),
    onSuccess: (_result, { eventId }) => invalidateEventDocumentRequirements(queryClient, eventId),
  });
}

export type EventDocumentRequirementCheck = {
  document_type: string;
  required: boolean;
  result: DocumentRequirementResult;
};

export type EventDocumentRequirementCheckList = {
  event_id: string;
  person_id: string;
  requirements: EventDocumentRequirementCheck[];
};

/** `GET /api/v1/events/{event_id}/document-requirements/{person_id}`
 * (events-api.md §31.3) — `event.read` + `document.read`. Every value in
 * `requirements[].result` is derived server-side; this hook never
 * computes `valid`/`missing`/`expired` itself.
 *
 * KNOWN CONTRACT GAP (Issue #175): there is no implemented endpoint that
 * lists an Event's participants (`GET /events/{event_id}/participants`
 * is documented at events-api.md §18/endpoint-inventory.md §8 but has no
 * route in `app/api/v1/events.py` — confirmed against the actual
 * router). The competition-package endpoint resolves the participant set
 * itself, server-side, from `EventParticipation`; nothing exposes that
 * same set for read/display. This check therefore cannot be
 * auto-enumerated into a roster — callers look up one already-identified
 * Person's readiness at a time (see EventDocumentPackageDialog). */
export function useEventDocumentRequirementCheck(
  eventId: string | undefined,
  personId: string | undefined,
) {
  return useQuery<EventDocumentRequirementCheckList, ApiError>({
    queryKey: ["events", "document-requirements", "check", eventId, personId],
    queryFn: () =>
      apiFetch<EventDocumentRequirementCheckList>(
        `/events/${eventId}/document-requirements/${personId}`,
      ),
    enabled: Boolean(eventId) && Boolean(personId),
  });
}

// --- Competition document package export (TH-0117.9, events-api.md §31.5) -

/** One entry of the 409 `document_package_incomplete` error's `details.
 * incomplete` array (`app/api/v1/events.py`'s `export_event_document_
 * package`) — safe, display-only fields; never a Person/Document/File
 * UUID or storage detail (events-api.md §31.5's explicit "No Person
 * UUID... is returned"). */
export type DocumentPackageIncompleteEntry = {
  participant_display_name: string;
  document_type: string;
  result: "missing" | "expired";
  required: boolean;
};

/** `POST /api/v1/events/{event_id}/document-package` (events-api.md
 * §31.5) — `event.read` + `document.export`. The participant set and
 * requirement evaluation are entirely server-side; this client never
 * sends a `person_id` list. A 409 `document_package_incomplete`
 * `ApiError` carries `details: { incomplete: DocumentPackageIncompleteEntry[]
 * }` — the caller re-calls with `confirmIncomplete: true` only after
 * explicit user confirmation (Issue #175). */
export function useExportEventDocumentPackage() {
  return useMutation<
    { blob: Blob; filename: string | null },
    ApiError,
    { eventId: string; confirmIncomplete: boolean }
  >({
    mutationFn: ({ eventId, confirmIncomplete }) =>
      apiFetchBlob(`/events/${eventId}/document-package`, {
        method: "POST",
        body: JSON.stringify({ confirm_incomplete: confirmIncomplete }),
      }),
  });
}
