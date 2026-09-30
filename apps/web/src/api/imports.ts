import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * Participant Import (TH-0118, docs/05-api/participant-import-api.md,
 * people-api.md §22) — thin hooks over the six existing
 * `/api/v1/memberships/imports` endpoints. The backend is authoritative
 * for everything: parsing, validation, duplicate detection, counters,
 * lifecycle and authorization. This module only sends requests and
 * returns what the backend says; it never validates rows, detects
 * duplicates or derives a lifecycle status itself.
 */

/** Canonical ImportJob statuses (people-api.md §22 "Import lifecycle"). */
export type ImportJobStatus =
  | "uploaded"
  | "parsing"
  | "validating"
  | "preview_ready"
  | "approved"
  | "applying"
  | "completed"
  | "partially_completed"
  | "failed"
  | "cancelled";

export type ImportJobStatistics = {
  /** `null` until the pipeline stage computing it has run — never a
   * fabricated 0 (backend `ImportJobStatisticsOut`). */
  total_records: number | null;
  valid_records: number | null;
  invalid_records: number | null;
  created_records: number | null;
  updated_records: number | null;
  skipped_records: number | null;
  /** `error`-severity entries only; warnings are not counted. */
  error_count: number;
};

export type ImportJob = {
  import_id: string;
  club_id: string;
  created_by_user_id: string;
  status: ImportJobStatus;
  format: string;
  statistics: ImportJobStatistics;
  created_at: string;
  updated_at: string;
};

export type ImportJobCreated = {
  import_id: string;
  status: ImportJobStatus;
  format: string;
  created_at: string;
};

export type ImportIssueSeverity = "error" | "warning";

export type ImportJobIssue = {
  id: string;
  row_number: number | null;
  field: string | null;
  code: string;
  message: string;
  severity: ImportIssueSeverity;
  /** Only the id of an existing Person matched by `duplicate_exact` —
   * never any of that Person's data. */
  matched_person_id: string | null;
  created_at: string;
};

const IMPORT_ISSUES_PAGE_SIZE = 50;

const TRANSIENT_STATUSES: ReadonlySet<ImportJobStatus> = new Set(["parsing", "validating", "applying"]);

export function useImportJob(importId: string | undefined) {
  return useQuery<ImportJob, ApiError>({
    queryKey: ["imports", "job", importId],
    queryFn: () => apiFetch<ImportJob>(`/memberships/imports/${importId}`),
    enabled: Boolean(importId),
    // Preview/apply run synchronously inside their own request; a job is
    // only seen in a transient stage when the page is (re)opened while
    // another request is still running — re-read it until it settles.
    refetchInterval: (query) =>
      query.state.data && TRANSIENT_STATUSES.has(query.state.data.status) ? 2000 : false,
  });
}

/** `GET .../errors` — the row-level report (errors and warnings), paginated
 * and optionally narrowed to one severity by the backend. */
export function useImportJobIssues(
  importId: string | undefined,
  params: { severity?: ImportIssueSeverity; page: number; enabled?: boolean },
) {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(IMPORT_ISSUES_PAGE_SIZE),
  });
  if (params.severity) query.set("severity", params.severity);
  return useQuery<CollectionResponse<ImportJobIssue>, ApiError>({
    queryKey: ["imports", "issues", importId, params.severity ?? "all", params.page],
    queryFn: () =>
      apiFetch<CollectionResponse<ImportJobIssue>>(
        `/memberships/imports/${importId}/errors?${query.toString()}`,
      ),
    enabled: Boolean(importId) && (params.enabled ?? true),
    placeholderData: keepPreviousData,
  });
}

function useImportJobTransition(action: "preview" | "approve" | "apply") {
  const queryClient = useQueryClient();
  return useMutation<ImportJob, ApiError, string>({
    mutationFn: (importId) =>
      apiFetch<ImportJob>(`/memberships/imports/${importId}/${action}`, { method: "POST" }),
    onSuccess: (job) => {
      queryClient.setQueryData(["imports", "job", job.import_id], job);
      void queryClient.invalidateQueries({ queryKey: ["imports", "issues", job.import_id] });
      if (action === "apply") {
        void queryClient.invalidateQueries({ queryKey: ["persons"] });
      }
    },
    onError: (_error, importId) => {
      // A failed preview moves the job to `failed` server-side (422
      // `import_validation_failed`); re-read it so the UI shows the
      // backend's own status and file-level errors.
      void queryClient.invalidateQueries({ queryKey: ["imports", "job", importId] });
      void queryClient.invalidateQueries({ queryKey: ["imports", "issues", importId] });
    },
  });
}

/** `POST /memberships/imports` — stores the file and creates the job in
 * `uploaded`. Never parses, validates or applies anything. */
export function useUploadImportFile() {
  return useMutation<ImportJobCreated, ApiError, File>({
    mutationFn: (file) => {
      const body = new FormData();
      body.append("file", file);
      return apiFetch<ImportJobCreated>("/memberships/imports", { method: "POST", body });
    },
  });
}

/** `POST .../preview` — parse + validate + duplicate detection (dry-run). */
export function usePreviewImport() {
  return useImportJobTransition("preview");
}

/** `POST .../approve` — explicit administrator approval; changes only the
 * job status. */
export function useApproveImport() {
  return useImportJobTransition("approve");
}

/** `POST .../apply` — the only step that creates domain data
 * (Person + User + ClubMembership per row in the current MVP). */
export function useApplyImport() {
  return useImportJobTransition("apply");
}
