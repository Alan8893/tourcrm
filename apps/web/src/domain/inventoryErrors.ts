/**
 * Russian text for Inventory («Склад») API errors. The backend decides
 * every rule and answers with a machine-readable `code`
 * (app/api/v1/inventory_errors.py; docs/05-api/endpoint-inventory.md §19);
 * this module only translates that answer for the Administrator — it
 * never predicts or re-checks a rule itself. An unknown code falls back
 * to the backend's own message.
 */

import type { ApiError } from "../api/client";

const CODE_MESSAGES: Record<string, string> = {
  // 409 — conflicts with the current state of a record.
  inventory_record_archived: "Запись в архиве и доступна только для чтения.",
  system_unit_immutable: "Системную единицу измерения нельзя изменить или архивировать.",
  accounting_mode_locked: "Режим учёта нельзя изменить: по позиции уже есть движения или экземпляры.",
  unit_locked: "Единицу измерения нельзя изменить: по позиции уже есть движения.",
  location_has_active_children: "Сначала архивируйте или перенесите вложенные места хранения.",
  location_has_instances: "В месте хранения есть экземпляры — сначала переместите их.",
  location_has_stock: "В месте хранения есть остаток — сначала переместите или спишите его.",
  item_has_active_instances: "У позиции есть экземпляры, которые не списаны.",
  item_has_stock: "У позиции есть ненулевой остаток.",
  item_has_outstanding_issues: "По позиции есть невозвращённые выдачи.",
  issue_cancelled: "Выдача отменена и больше не изменяется.",
  issue_fully_returned: "Выдача полностью возвращена и больше не изменяется.",
  return_exceeds_outstanding: "Нельзя вернуть больше, чем числится выданным.",
  instance_not_issued: "Экземпляр не числится выданным по этой выдаче.",
  issue_line_outstanding: "По строке ещё что-то числится выданным — сначала оформите возврат.",
  issue_line_removed: "Строка уже удалена из выдачи.",
  insufficient_stock: "Недостаточно остатка для операции.",
  invalid_state_transition: "Операция недоступна в текущем состоянии экземпляра.",
  instance_written_off: "Экземпляр списан и доступен только для чтения.",
  write_off_already_reversed: "Это списание уже отменено.",
  name_conflict: "Запись с таким названием уже есть.",
  // 422 — the request itself is unusable.
  archived_reference: "Выбранная запись находится в архиве.",
  invalid_reference: "Выбранная запись не найдена.",
  invalid_parent: "Место хранения нельзя вложить в само себя или в своё вложенное место.",
  item_not_instance_mode: "Позиция ведётся в количественном учёте.",
  item_not_quantity_mode: "Позиция ведётся в поэкземплярном учёте.",
  invalid_reversal_target: "Это движение нельзя отменить.",
  invalid_recipient: "Получатель не подходит для выдачи.",
  storage_location_required: "Исходное место хранения в архиве — выберите другое место.",
  storage_location_not_allowed: "Исходное место хранения активно — другое место выбрать нельзя.",
  invalid_inventory_data: "Проверьте введённые данные.",
};

/** The message to show for a failed inventory mutation. */
export function inventoryErrorMessage(error: ApiError): string {
  if (error.status === 403) return "Операция доступна только администратору.";
  if (error.status === 404) return "Запись не найдена — возможно, её уже изменили. Обновите страницу.";
  if (error.status === 401) return "Сессия завершилась. Войдите снова.";
  const known = CODE_MESSAGES[error.code];
  if (known) return known;
  if (error.status === 422 || error.status === 400) return "Проверьте введённые данные.";
  if (error.status >= 500) return "Сервер не смог выполнить операцию. Попробуйте ещё раз.";
  return error.message;
}

/** A network failure never reaches `apiFetch`'s error mapping: `fetch`
 * rejects with a `TypeError`. Normalise anything thrown into a message. */
export function mutationErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "status" in error && "code" in error) {
    return inventoryErrorMessage(error as ApiError);
  }
  return "Нет связи с сервером. Проверьте подключение и попробуйте ещё раз.";
}
