import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * Achievements administration (Issue #220;
 * docs/04-modules/achievements-and-norms.md, decisions A1–A12).
 *
 * Transport only. Every business rule — lifecycle, versioning and
 * immutability, the approved metric catalog, rule validation, manual
 * award eligibility, Member-only recipients, revocation — is decided by
 * the backend; this module never evaluates a rule or pre-filters what the
 * backend allows. The metric catalog and condition operators are read
 * from `GET /achievements/rule-catalog`, never hardcoded here.
 */

export type AchievementSource = "club" | "fstr";
export type DefinitionAwardMethod = "automatic" | "manual" | "both";
export type AwardMethod = "automatic" | "manual";
export type Repeatability = "non_repeatable" | "repeatable";
export type LifecycleStatus = "active" | "inactive";
export type AwardStatus = "active" | "revoked";

export type RuleLeaf = { metric: string; operator: string; value: number };
export type RuleGroup = { logic: string; conditions: RuleNode[] };
export type RuleNode = RuleLeaf | RuleGroup;

export function isRuleGroup(node: RuleNode): node is RuleGroup {
  return "conditions" in node;
}

export type RuleCatalog = {
  metrics: { code: string; label: string; description: string }[];
  logic_operators: string[];
  comparison_operators: string[];
  max_depth: number;
};

export type AchievementDefinition = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  source: AchievementSource;
  award_method: DefinitionAwardMethod;
  repeatability: Repeatability;
  status: LifecycleStatus;
  created_at: string;
  updated_at: string;
};

export type RuleVersion = {
  id: string;
  definition_id: string;
  version_number: number;
  condition: RuleNode;
  normative_set_version_id: string | null;
  status: LifecycleStatus;
  is_used: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type NormativeSet = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};

export type NormativeVersionFields = {
  source_organization: string;
  document_title: string;
  source_url: string;
  document_version: string;
  publication_date: string | null;
  effective_from: string;
  effective_to: string | null;
};

export type NormativeVersion = NormativeVersionFields & {
  id: string;
  normative_set_id: string;
  version_number: number;
  status: LifecycleStatus;
  is_used: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type AchievementAward = {
  id: string;
  definition_id: string;
  definition_code: string;
  definition_name: string;
  definition_source: AchievementSource;
  definition_repeatability: Repeatability;
  person_id: string;
  person_name: string;
  award_method: AwardMethod;
  rule_version_id: string | null;
  rule_version_number: number | null;
  normative_set_version_id: string | null;
  normative_version_number: number | null;
  awarded_at: string;
  awarded_by_user_id: string | null;
  verification_note: string | null;
  evaluation_trigger: "event" | "reconciliation" | null;
  evaluated_metrics: Record<string, number | null> | null;
  status: AwardStatus;
  revoked_at: string | null;
  revoked_by_user_id: string | null;
  revocation_reason: string | null;
};

export type ReconciliationResult = {
  evaluated_people: number;
  evaluated_rules: number;
  awards_created: number;
};

const BASE = "/achievements";
const PAGE_SIZE = 50;

function post<T>(path: string, body: unknown = {}): Promise<T> {
  return apiFetch<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(`${BASE}${path}`, { method: "PATCH", body: JSON.stringify(body) });
}

function useInvalidateAchievements() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["achievements"] });
}

// --- reads ----------------------------------------------------------------------------

export function useRuleCatalog() {
  return useQuery<RuleCatalog, ApiError>({
    queryKey: ["achievements", "rule-catalog"],
    queryFn: () => apiFetch<RuleCatalog>(`${BASE}/rule-catalog`),
    staleTime: Infinity,
  });
}

export function useDefinitions(params: { page: number; status?: LifecycleStatus | "" }) {
  const query = new URLSearchParams({ page: String(params.page), page_size: String(PAGE_SIZE) });
  if (params.status) query.set("status", params.status);
  return useQuery<CollectionResponse<AchievementDefinition>, ApiError>({
    queryKey: ["achievements", "definitions", params.page, params.status ?? ""],
    queryFn: () =>
      apiFetch<CollectionResponse<AchievementDefinition>>(`${BASE}/definitions?${query.toString()}`),
    placeholderData: keepPreviousData,
  });
}

/** Every Definition for pickers (manual award) — the backend caps a page
 * at 100 items. */
export function useAllActiveDefinitions() {
  return useQuery<CollectionResponse<AchievementDefinition>, ApiError>({
    queryKey: ["achievements", "definitions", "active-all"],
    queryFn: () =>
      apiFetch<CollectionResponse<AchievementDefinition>>(
        `${BASE}/definitions?status=active&page=1&page_size=100`,
      ),
  });
}

export function useDefinition(definitionId: string | undefined) {
  return useQuery<AchievementDefinition, ApiError>({
    queryKey: ["achievements", "definition", definitionId],
    queryFn: () =>
      apiFetch<AchievementDefinition>(`${BASE}/definitions/${encodeURIComponent(definitionId ?? "")}`),
    enabled: Boolean(definitionId),
  });
}

export function useRuleVersions(definitionId: string | undefined) {
  return useQuery<CollectionResponse<RuleVersion>, ApiError>({
    queryKey: ["achievements", "rule-versions", definitionId],
    queryFn: () =>
      apiFetch<CollectionResponse<RuleVersion>>(
        `${BASE}/definitions/${encodeURIComponent(definitionId ?? "")}/rule-versions`,
      ),
    enabled: Boolean(definitionId),
  });
}

export function useNormativeSets() {
  return useQuery<CollectionResponse<NormativeSet>, ApiError>({
    queryKey: ["achievements", "normative-sets"],
    queryFn: () => apiFetch<CollectionResponse<NormativeSet>>(`${BASE}/normative-sets?page_size=100`),
  });
}

export function useNormativeSet(setId: string | undefined) {
  return useQuery<NormativeSet, ApiError>({
    queryKey: ["achievements", "normative-set", setId],
    queryFn: () => apiFetch<NormativeSet>(`${BASE}/normative-sets/${encodeURIComponent(setId ?? "")}`),
    enabled: Boolean(setId),
  });
}

export function useNormativeVersions(setId: string | undefined) {
  return useQuery<CollectionResponse<NormativeVersion>, ApiError>({
    queryKey: ["achievements", "normative-versions", setId],
    queryFn: () =>
      apiFetch<CollectionResponse<NormativeVersion>>(
        `${BASE}/normative-sets/${encodeURIComponent(setId ?? "")}/versions`,
      ),
    enabled: Boolean(setId),
  });
}

export type AwardFilters = {
  page: number;
  definitionId?: string;
  status?: AwardStatus | "";
  awardMethod?: AwardMethod | "";
};

export function useAwards(filters: AwardFilters) {
  const query = new URLSearchParams({ page: String(filters.page), page_size: String(PAGE_SIZE) });
  if (filters.definitionId) query.set("definition_id", filters.definitionId);
  if (filters.status) query.set("status", filters.status);
  if (filters.awardMethod) query.set("award_method", filters.awardMethod);
  return useQuery<CollectionResponse<AchievementAward>, ApiError>({
    queryKey: [
      "achievements",
      "awards",
      filters.page,
      filters.definitionId ?? "",
      filters.status ?? "",
      filters.awardMethod ?? "",
    ],
    queryFn: () => apiFetch<CollectionResponse<AchievementAward>>(`${BASE}/awards?${query.toString()}`),
    placeholderData: keepPreviousData,
  });
}

// --- mutations ----------------------------------------------------------------------

export type DefinitionCreateFields = {
  code: string;
  name: string;
  description: string | null;
  source: AchievementSource;
  award_method: DefinitionAwardMethod;
  repeatability: Repeatability;
};

export function useCreateDefinition() {
  const invalidate = useInvalidateAchievements();
  return useMutation<AchievementDefinition, ApiError, DefinitionCreateFields>({
    mutationFn: (fields) => post<AchievementDefinition>("/definitions", fields),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateDefinition() {
  const invalidate = useInvalidateAchievements();
  return useMutation<
    AchievementDefinition,
    ApiError,
    { definitionId: string; fields: { name?: string; description?: string | null } }
  >({
    mutationFn: ({ definitionId, fields }) =>
      patch<AchievementDefinition>(`/definitions/${encodeURIComponent(definitionId)}`, fields),
    onSuccess: () => invalidate(),
  });
}

export function useSetDefinitionStatus() {
  const invalidate = useInvalidateAchievements();
  return useMutation<AchievementDefinition, ApiError, { definitionId: string; active: boolean }>({
    mutationFn: ({ definitionId, active }) =>
      post<AchievementDefinition>(
        `/definitions/${encodeURIComponent(definitionId)}/${active ? "activate" : "deactivate"}`,
      ),
    onSuccess: () => invalidate(),
  });
}

export type RuleVersionFields = {
  condition: RuleNode;
  normative_set_version_id: string | null;
};

export function useCreateRuleVersion() {
  const invalidate = useInvalidateAchievements();
  return useMutation<RuleVersion, ApiError, { definitionId: string; fields: RuleVersionFields }>({
    mutationFn: ({ definitionId, fields }) =>
      post<RuleVersion>(`/definitions/${encodeURIComponent(definitionId)}/rule-versions`, fields),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateRuleVersion() {
  const invalidate = useInvalidateAchievements();
  return useMutation<RuleVersion, ApiError, { ruleVersionId: string; fields: RuleVersionFields }>({
    mutationFn: ({ ruleVersionId, fields }) =>
      patch<RuleVersion>(`/rule-versions/${encodeURIComponent(ruleVersionId)}`, fields),
    onSuccess: () => invalidate(),
  });
}

export function useSetRuleVersionStatus() {
  const invalidate = useInvalidateAchievements();
  return useMutation<RuleVersion, ApiError, { ruleVersionId: string; active: boolean }>({
    mutationFn: ({ ruleVersionId, active }) =>
      post<RuleVersion>(
        `/rule-versions/${encodeURIComponent(ruleVersionId)}/${active ? "activate" : "deactivate"}`,
      ),
    onSuccess: () => invalidate(),
  });
}

export function useCreateNormativeSet() {
  const invalidate = useInvalidateAchievements();
  return useMutation<NormativeSet, ApiError, { code: string; name: string; description: string | null }>({
    mutationFn: (fields) => post<NormativeSet>("/normative-sets", fields),
    onSuccess: () => invalidate(),
  });
}

export function useCreateNormativeVersion() {
  const invalidate = useInvalidateAchievements();
  return useMutation<NormativeVersion, ApiError, { setId: string; fields: NormativeVersionFields }>({
    mutationFn: ({ setId, fields }) =>
      post<NormativeVersion>(`/normative-sets/${encodeURIComponent(setId)}/versions`, fields),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateNormativeVersion() {
  const invalidate = useInvalidateAchievements();
  return useMutation<NormativeVersion, ApiError, { versionId: string; fields: NormativeVersionFields }>({
    mutationFn: ({ versionId, fields }) =>
      patch<NormativeVersion>(`/normative-versions/${encodeURIComponent(versionId)}`, fields),
    onSuccess: () => invalidate(),
  });
}

export function useSetNormativeVersionStatus() {
  const invalidate = useInvalidateAchievements();
  return useMutation<NormativeVersion, ApiError, { versionId: string; active: boolean }>({
    mutationFn: ({ versionId, active }) =>
      post<NormativeVersion>(
        `/normative-versions/${encodeURIComponent(versionId)}/${active ? "activate" : "deactivate"}`,
      ),
    onSuccess: () => invalidate(),
  });
}

export function useCreateManualAward() {
  const invalidate = useInvalidateAchievements();
  return useMutation<
    AchievementAward,
    ApiError,
    { definition_id: string; person_id: string; verification_note: string | null }
  >({
    mutationFn: (fields) => post<AchievementAward>("/awards", fields),
    onSuccess: () => invalidate(),
  });
}

export function useRevokeAward() {
  const invalidate = useInvalidateAchievements();
  return useMutation<AchievementAward, ApiError, { awardId: string; reason: string }>({
    mutationFn: ({ awardId, reason }) =>
      post<AchievementAward>(`/awards/${encodeURIComponent(awardId)}/revoke`, { reason }),
    onSuccess: () => invalidate(),
  });
}

export function useRunReconciliation() {
  const invalidate = useInvalidateAchievements();
  return useMutation<ReconciliationResult, ApiError, void>({
    mutationFn: () => post<ReconciliationResult>("/reconciliation"),
    onSuccess: () => invalidate(),
  });
}
