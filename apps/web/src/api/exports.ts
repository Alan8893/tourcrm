import { useMutation, useQuery } from "@tanstack/react-query";

import { apiFetch, apiFetchBlob, ApiError } from "./client";

/**
 * Participant Export (TH-0118.4, docs/05-api/participant-export-api.md) —
 * hooks over the two existing endpoints:
 *
 *   GET  /api/v1/memberships/exports/fields   backend field allowlist
 *   POST /api/v1/memberships/exports          one dataset, xlsx|pdf|print
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

export type ParticipantExportRequest = {
  context: ExportContext;
  group_id?: string;
  event_id?: string;
  membership_status?: string;
  participation_status?: string;
  fields: string[];
  format: ExportFormat;
};

export function useExportFields() {
  return useQuery<ExportFieldsResponse, ApiError>({
    queryKey: ["exports", "fields"],
    queryFn: () => apiFetch<ExportFieldsResponse>("/memberships/exports/fields"),
    staleTime: 5 * 60 * 1000,
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
