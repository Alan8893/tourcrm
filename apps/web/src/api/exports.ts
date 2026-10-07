import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch, apiFetchBlob, ApiError, type Pagination } from "./client";

/**
 * Participant Export (TH-0118.4, docs/05-api/participant-export-api.md) —
 * hooks over the existing export endpoints:
 *
 *   GET  /api/v1/memberships/exports/fields   backend field allowlist
 *   GET  /api/v1/memberships/exports/filters  backend filter vocabularies
 *   POST /api/v1/memberships/exports          one dataset, xlsx|pdf|print
 *   POST /api/v1/memberships/exports/preview  one page of the same dataset
 *
 * The backend resolves the dataset from the context (never from a list of
 * person ids), re-validates every field and filter, and authorizes every
 * request. This module keeps no field list of its own and never builds or
 * intersects a dataset.
 */

export type ExportContext = "club" | "group" | "event" | "group_event";
export type ExportFormat = "xlsx" | "pdf" | "print";

export type ExportField = {
  field_code: string;
  label: string;
  /** Export contexts the backend offers this field in. */
  contexts: ExportContext[];
};

export type ExportFieldsResponse = {
  items: ExportField[];
};

/** One backend-provided filter value with its display label. */
export type ExportFilterOption = {
  value: string;
  label: string;
};

export type ExportFiltersResponse = {
  /** Canonical `participation_status` values — the frontend keeps no list
   * of its own and never falls back to one. */
  participation_status: ExportFilterOption[];
};

/** The dataset selection — identical for the export and its preview. */
export type ParticipantExportSelection = {
  context: ExportContext;
  group_id?: string;
  event_id?: string;
  membership_status?: string;
  participation_status?: string;
  fields: string[];
};

export type ParticipantExportRequest = ParticipantExportSelection & {
  format: ExportFormat;
};

export type ParticipantExportPreview = {
  title: string;
  /** The requested allowlisted fields, in request order. */
  columns: { field_code: string; label: string }[];
  /** One text cell per column, exactly as the PDF/print formats show it. */
  items: string[][];
  pagination: Pagination;
};

export function useExportFields() {
  return useQuery<ExportFieldsResponse, ApiError>({
    queryKey: ["exports", "fields"],
    queryFn: () => apiFetch<ExportFieldsResponse>("/memberships/exports/fields"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useExportFilters(enabled = true) {
  return useQuery<ExportFiltersResponse, ApiError>({
    queryKey: ["exports", "filters"],
    queryFn: () => apiFetch<ExportFiltersResponse>("/memberships/exports/filters"),
    staleTime: 5 * 60 * 1000,
    enabled,
  });
}

/** One mutation for all three formats: the request is identical except for
 * `format`, which only selects the backend renderer (§7). */
export function useRunParticipantExport() {
  return useMutation<{ blob: Blob; filename: string | null }, ApiError, ParticipantExportRequest>({
    mutationFn: (request) =>
      apiFetchBlob("/memberships/exports", { method: "POST", body: JSON.stringify(request) }),
  });
}

/** Issue #299 «Участники мероприятий»: one backend page of the canonical
 * export dataset for the selection — the same selection the XLSX/PDF/Print
 * export sends. A read, though carried by POST (the selection is a JSON
 * body); `null` disables the query until the selection is complete. */
export function useParticipantExportPreview(
  selection: ParticipantExportSelection | null,
  page: number,
  pageSize = 50,
) {
  return useQuery<ParticipantExportPreview, ApiError>({
    queryKey: ["exports", "preview", selection, page, pageSize],
    queryFn: () =>
      apiFetch<ParticipantExportPreview>("/memberships/exports/preview", {
        method: "POST",
        body: JSON.stringify({ ...selection, page, page_size: pageSize }),
      }),
    enabled: selection !== null,
  });
}
