/**
 * Russian labels and messages for the Achievements administration UI
 * (Issue #220). Presentation only: every rule is decided by the backend
 * (app/achievements), which answers with a machine-readable error `code`;
 * this module only translates values and answers for the Administrator —
 * it never predicts, evaluates or re-checks a rule itself.
 */

import type { ApiError } from "../api/client";
import type { StatusIconId } from "../assets/icons";
import {
  isRuleGroup,
  type AchievementSource,
  type AwardMethod,
  type AwardStatus,
  type DefinitionAwardMethod,
  type LifecycleStatus,
  type Repeatability,
  type RuleCatalog,
  type RuleNode,
} from "../api/achievements";

export const SOURCE_LABELS: Record<AchievementSource, string> = {
  club: "Клубное",
  fstr: "ФСТР",
};

export const DEFINITION_AWARD_METHOD_LABELS: Record<DefinitionAwardMethod, string> = {
  automatic: "Автоматически",
  manual: "Вручную",
  both: "Автоматически и вручную",
};

export const AWARD_METHOD_LABELS: Record<AwardMethod, string> = {
  automatic: "Автоматически (движок)",
  manual: "Вручную (проверено администратором)",
};

export const REPEATABILITY_LABELS: Record<Repeatability, string> = {
  non_repeatable: "Однократное",
  repeatable: "Повторяемое",
};

export const LIFECYCLE_LABELS: Record<LifecycleStatus, string> = {
  active: "Активно",
  inactive: "Неактивно",
};

export const AWARD_STATUS_LABELS: Record<AwardStatus, string> = {
  active: "Действует",
  revoked: "Отозвано",
};

export const TRIGGER_LABELS: Record<"event" | "reconciliation", string> = {
  event: "по событию",
  reconciliation: "при сверке",
};

export function lifecycleIcon(status: LifecycleStatus): StatusIconId {
  return status === "active" ? "status.success" : "status.archived";
}

export function awardStatusIcon(status: AwardStatus): StatusIconId {
  return status === "active" ? "status.success" : "status.error";
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(
    date,
  );
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const LOGIC_LABELS: Record<string, string> = { AND: "И", OR: "ИЛИ" };

export function logicLabel(logic: string): string {
  return LOGIC_LABELS[logic] ?? logic;
}

/** Human-readable text of a stored condition tree (display only). */
export function describeRule(node: RuleNode, catalog?: RuleCatalog): string {
  if (isRuleGroup(node)) {
    const parts = node.conditions.map((child) =>
      isRuleGroup(child) ? `(${describeRule(child, catalog)})` : describeRule(child, catalog),
    );
    return parts.join(` ${logicLabel(node.logic)} `);
  }
  const label = catalog?.metrics.find((metric) => metric.code === node.metric)?.label ?? node.metric;
  return `${label} ${node.operator} ${node.value}`;
}

const CODE_MESSAGES: Record<string, string> = {
  duplicate_code: "Запись с таким кодом уже существует.",
  version_in_use: "Версия нормативов уже использована в выданных достижениях и не изменяется — создайте новую версию.",
  rule_version_definition_mismatch: "Выбранная версия правила относится к другому достижению.",
  verification_note_required: "Для ручной проверки по ФСТР или нормативам укажите основание / результат проверки.",
  definition_inactive: "Достижение неактивно: новые выдачи невозможны.",
  manual_award_not_allowed: "Это достижение выдаётся только автоматически.",
  already_awarded: "Это однократное достижение уже выдавалось этому участнику.",
  award_already_revoked: "Выдача уже отозвана.",
  revocation_reason_required: "Укажите причину отзыва.",
  recipient_not_member: "Достижение может получить только участник (роль «Участник»).",
  unsupported_metric: "Правило использует показатель, который пока не утверждён для расчёта.",
  invalid_rule: "Правило составлено неверно — проверьте условия.",
  normative_reference_required: "Для достижения ФСТР выберите версию нормативов.",
  invalid_value: "Проверьте введённые данные.",
};

/** The message to show for a failed achievements request. */
export function achievementErrorMessage(error: unknown): string {
  if (!(error && typeof error === "object" && "status" in error && "code" in error)) {
    return "Нет связи с сервером. Проверьте подключение и попробуйте ещё раз.";
  }
  const apiError = error as ApiError;
  if (apiError.status === 403) return "Операция доступна только администратору.";
  if (apiError.status === 401) return "Сессия завершилась. Войдите снова.";
  if (apiError.status === 404) return "Запись не найдена — возможно, её уже изменили. Обновите страницу.";
  const known = CODE_MESSAGES[apiError.code];
  if (known) return known;
  if (apiError.status === 422 || apiError.status === 400) return "Проверьте введённые данные.";
  if (apiError.status >= 500) return "Сервер не смог выполнить операцию. Попробуйте ещё раз.";
  return apiError.message;
}
