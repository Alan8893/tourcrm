import { useId, useState, type FormEvent, type ReactNode } from "react";

import type { ApiError } from "../api/client";
import {
  useArchiveInventoryCategory,
  useArchiveInventoryItem,
  useArchiveInventoryLocation,
  useArchiveInventoryUnit,
  useCreateInventoryCategory,
  useCreateInventoryInstance,
  useCreateInventoryItem,
  useCreateInventoryLocation,
  useCreateInventoryUnit,
  useInstanceRepair,
  useReceiveInventoryQuantity,
  useReverseInventoryWriteOff,
  useTransferInventoryInstance,
  useTransferInventoryQuantity,
  useUpdateInventoryCategory,
  useUpdateInventoryInstance,
  useUpdateInventoryItem,
  useUpdateInventoryLocation,
  useUpdateInventoryUnit,
  useWriteOffInventoryInstance,
  useWriteOffInventoryQuantity,
  type AccountingMode,
  type InventoryCategory,
  type InventoryInstance,
  type InventoryItem,
  type InventoryItemFields,
  type InventoryMovement,
  type InventoryStock,
  type InventoryStorageLocation,
  type InventoryUnit,
} from "../api/inventory";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { Input } from "../components/ui/Input";
import inputStyles from "../components/ui/Input.module.css";
import { useNotify } from "../components/ui/notificationContext";
import {
  accountingModeLabel,
  formatQuantity,
  locationPath,
  minorToRublesInput,
  parseQuantity,
  parseRublesToMinor,
} from "../domain/inventoryFormat";
import { inventoryErrorMessage, mutationErrorMessage } from "../domain/inventoryErrors";
import { unitName, type InventoryLookups } from "../hooks/useInventoryLookups";
import { activeLocationOptions, byLabel, type Option } from "./inventoryHelpers";
import styles from "./Inventory.module.css";

/**
 * Administrator dialogs for the Inventory operations
 * (docs/05-api/endpoint-inventory.md §19). Each dialog collects exactly
 * the fields of one documented request, sends it, and shows the
 * backend's answer: a success toast and refetched data, or the mapped
 * domain error inside the dialog. No dialog predicts a rule (stock,
 * state transitions, archivability) on its own.
 *
 * Parents mount a dialog only while it is open, so every opening starts
 * from a fresh form.
 */

// --- form primitives -------------------------------------------------------------

type FormDialogProps = {
  title: string;
  description?: string;
  submitLabel: string;
  pending: boolean;
  error: string | null;
  canSubmit?: boolean;
  destructive?: boolean;
  onSubmit: () => void;
  onClose: () => void;
  children?: ReactNode;
};

/** The shared `Dialog` with a `<form>`: Enter submits, the action row
 * wraps on a phone, and a failed request is shown in place. */
export function FormDialog({
  title,
  description,
  submitLabel,
  pending,
  error,
  canSubmit = true,
  destructive = false,
  onSubmit,
  onClose,
  children,
}: FormDialogProps) {
  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSubmit && !pending) onSubmit();
  }

  return (
    <Dialog open title={title} description={description} onClose={pending ? () => {} : onClose}>
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        {children}
        {error ? (
          <p className={styles.formError} role="alert">
            {error}
          </p>
        ) : null}
        <div className={styles.formActions}>
          <Button type="button" variant="secondary" onClick={onClose} disabled={pending}>
            Отмена
          </Button>
          <Button
            type="submit"
            variant={destructive ? "destructive" : "primary"}
            disabled={!canSubmit || pending}
          >
            {submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

type SelectFieldProps = {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
  disabled?: boolean;
};

export function SelectField({ label, value, options, onChange, placeholder, hint, disabled }: SelectFieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={inputStyles.field}>
      <label className={inputStyles.label} htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className={inputStyles.select}
        value={value}
        disabled={disabled}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      >
        {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? (
        <span className={inputStyles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export function TextAreaField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={inputStyles.field}>
      <label className={inputStyles.label} htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className={styles.textarea}
        value={value}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? (
        <span className={inputStyles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

/** Active records for a picker; the record's current (possibly archived)
 * value stays visible so an edit form never silently changes it. */
function referenceOptions(
  records: Iterable<{ id: string; name: string; status: string }>,
  currentId?: string,
): Option[] {
  return [...records]
    .filter((record) => record.status === "active" || record.id === currentId)
    .map((record) => ({
      value: record.id,
      label: record.status === "active" ? record.name : `${record.name} (в архиве)`,
    }))
    .sort(byLabel);
}

function blankToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** Runs one mutation for a dialog: success → toast + close; failure →
 * the mapped backend error inside the dialog. */
function useDialogSubmit(onClose: () => void) {
  const notify = useNotify();
  const [error, setError] = useState<string | null>(null);
  return {
    error,
    setError,
    handlers: (successMessage: string, after?: () => void) => ({
      onSuccess: () => {
        notify("success", successMessage);
        after?.();
        onClose();
      },
      onError: (failure: unknown) => setError(mutationErrorMessage(failure)),
    }),
  };
}

// --- nomenclature ----------------------------------------------------------------

const ACCOUNTING_MODE_OPTIONS: Option[] = (["quantity", "instance"] as AccountingMode[]).map((mode) => ({
  value: mode,
  label: accountingModeLabel(mode),
}));

export function ItemFormDialog({
  item,
  lookups,
  onClose,
}: {
  item?: InventoryItem;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [name, setName] = useState(item?.name ?? "");
  const [categoryId, setCategoryId] = useState(item?.category_id ?? "");
  const [unitId, setUnitId] = useState(item?.unit_id ?? "");
  const [mode, setMode] = useState<AccountingMode>(item?.accounting_mode ?? "quantity");
  const [cost, setCost] = useState(minorToRublesInput(item?.current_cost_minor ?? null));
  const create = useCreateInventoryItem();
  const update = useUpdateInventoryItem();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const costMinor = parseRublesToMinor(cost);
  const costInvalid = Number.isNaN(costMinor);
  const canSubmit = Boolean(name.trim() && categoryId && unitId) && !costInvalid;

  function submit() {
    const fields: InventoryItemFields = {
      name: name.trim(),
      category_id: categoryId,
      unit_id: unitId,
      accounting_mode: mode,
      current_cost_minor: costMinor,
    };
    setError(null);
    if (!item) {
      create.mutate(fields, handlers(`Позиция «${fields.name}» создана`));
      return;
    }
    const changed: Partial<InventoryItemFields> = {};
    if (fields.name !== item.name) changed.name = fields.name;
    if (fields.category_id !== item.category_id) changed.category_id = fields.category_id;
    if (fields.unit_id !== item.unit_id) changed.unit_id = fields.unit_id;
    if (fields.accounting_mode !== item.accounting_mode) changed.accounting_mode = fields.accounting_mode;
    if (fields.current_cost_minor !== item.current_cost_minor) changed.current_cost_minor = fields.current_cost_minor;
    if (Object.keys(changed).length === 0) {
      onClose();
      return;
    }
    update.mutate({ id: item.id, fields: changed }, handlers(`Позиция «${fields.name}» сохранена`));
  }

  return (
    <FormDialog
      title={item ? "Изменить позицию" : "Новая позиция"}
      description={
        item
          ? "Режим учёта и единицу нельзя изменить после первого движения — это проверит сервер."
          : "Позиция каталога склада: вид имущества."
      }
      submitLabel={item ? "Сохранить" : "Создать"}
      pending={create.isPending || update.isPending}
      error={error}
      canSubmit={canSubmit}
      onSubmit={submit}
      onClose={onClose}
    >
      <Input label="Наименование" value={name} onChange={(event) => setName(event.target.value)} required />
      <SelectField
        label="Категория"
        value={categoryId}
        placeholder="Выберите категорию"
        options={referenceOptions(lookups.categories.values(), item?.category_id)}
        onChange={setCategoryId}
        hint={lookups.categories.size === 0 ? "Создайте категорию во вкладке «Справочники»." : undefined}
      />
      <SelectField
        label="Единица измерения"
        value={unitId}
        placeholder="Выберите единицу"
        options={referenceOptions(lookups.units.values(), item?.unit_id)}
        onChange={setUnitId}
      />
      <SelectField
        label="Режим учёта"
        value={mode}
        options={ACCOUNTING_MODE_OPTIONS}
        onChange={(value) => setMode(value as AccountingMode)}
      />
      <Input
        label="Стоимость позиции, ₽"
        value={cost}
        inputMode="decimal"
        onChange={(event) => setCost(event.target.value)}
        hint={costInvalid ? "Введите сумму в рублях, например 1250 или 1250,50." : "Необязательно."}
        aria-invalid={costInvalid}
      />
    </FormDialog>
  );
}

// --- categories and units --------------------------------------------------------

type ReferenceKind = "category" | "unit";

const REFERENCE_TEXT: Record<ReferenceKind, { create: string; edit: string; created: string; saved: string }> = {
  category: {
    create: "Новая категория",
    edit: "Переименовать категорию",
    created: "Категория создана",
    saved: "Категория сохранена",
  },
  unit: {
    create: "Новая единица измерения",
    edit: "Переименовать единицу",
    created: "Единица измерения создана",
    saved: "Единица измерения сохранена",
  },
};

export function ReferenceNameDialog({
  kind,
  record,
  onClose,
}: {
  kind: ReferenceKind;
  record?: InventoryCategory | InventoryUnit;
  onClose: () => void;
}) {
  const [name, setName] = useState(record?.name ?? "");
  const createCategory = useCreateInventoryCategory();
  const updateCategory = useUpdateInventoryCategory();
  const createUnit = useCreateInventoryUnit();
  const updateUnit = useUpdateInventoryUnit();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const text = REFERENCE_TEXT[kind];
  const pending = [createCategory, updateCategory, createUnit, updateUnit].some((m) => m.isPending);

  function submit() {
    const trimmed = name.trim();
    setError(null);
    if (record) {
      const mutation = kind === "category" ? updateCategory : updateUnit;
      mutation.mutate({ id: record.id, name: trimmed }, handlers(text.saved));
    } else {
      const mutation = kind === "category" ? createCategory : createUnit;
      mutation.mutate({ name: trimmed }, handlers(text.created));
    }
  }

  return (
    <FormDialog
      title={record ? text.edit : text.create}
      submitLabel={record ? "Сохранить" : "Создать"}
      pending={pending}
      error={error}
      canSubmit={Boolean(name.trim())}
      onSubmit={submit}
      onClose={onClose}
    >
      <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} required />
    </FormDialog>
  );
}

// --- archive ---------------------------------------------------------------------

export type ArchiveTarget =
  | { kind: "item"; record: InventoryItem }
  | { kind: "category"; record: InventoryCategory }
  | { kind: "unit"; record: InventoryUnit }
  | { kind: "location"; record: InventoryStorageLocation };

const ARCHIVE_TITLE: Record<ArchiveTarget["kind"], string> = {
  item: "Архивировать позицию?",
  category: "Архивировать категорию?",
  unit: "Архивировать единицу измерения?",
  location: "Архивировать место хранения?",
};

/** Archiving is irreversible (§17); whether it is allowed right now
 * (stock, instances, outstanding issues, child locations) is the
 * backend's answer, shown in the dialog. */
export function ArchiveDialog({ target, onClose }: { target: ArchiveTarget; onClose: () => void }) {
  const item = useArchiveInventoryItem();
  const category = useArchiveInventoryCategory();
  const unit = useArchiveInventoryUnit();
  const location = useArchiveInventoryLocation();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const mutation = { item, category, unit, location }[target.kind];

  return (
    <FormDialog
      title={ARCHIVE_TITLE[target.kind]}
      description={`«${target.record.name}» станет доступна только для чтения. Архивирование необратимо.`}
      submitLabel="Архивировать"
      destructive
      pending={mutation.isPending}
      error={error}
      onSubmit={() => {
        setError(null);
        mutation.mutate(target.record.id, handlers(`«${target.record.name}» перенесено в архив`));
      }}
      onClose={onClose}
    />
  );
}

// --- storage locations -----------------------------------------------------------

export function LocationFormDialog({
  location,
  parentId: initialParentId = "",
  lookups,
  onClose,
}: {
  location?: InventoryStorageLocation;
  parentId?: string;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [name, setName] = useState(location?.name ?? "");
  const [parentId, setParentId] = useState(location?.parent_id ?? initialParentId);
  const create = useCreateInventoryLocation();
  const update = useUpdateInventoryLocation();
  const { error, setError, handlers } = useDialogSubmit(onClose);

  function submit() {
    const trimmed = name.trim();
    const parent = parentId || null;
    setError(null);
    if (!location) {
      create.mutate({ name: trimmed, parent_id: parent }, handlers(`Место «${trimmed}» создано`));
      return;
    }
    const fields: { name?: string; parent_id?: string | null } = {};
    if (trimmed !== location.name) fields.name = trimmed;
    if (parent !== location.parent_id) fields.parent_id = parent;
    if (Object.keys(fields).length === 0) {
      onClose();
      return;
    }
    update.mutate({ id: location.id, fields }, handlers(`Место «${trimmed}» сохранено`));
  }

  return (
    <FormDialog
      title={location ? "Изменить место хранения" : "Новое место хранения"}
      description="Места образуют иерархию любой глубины: склад, стеллаж, полка, ячейка…"
      submitLabel={location ? "Сохранить" : "Создать"}
      pending={create.isPending || update.isPending}
      error={error}
      canSubmit={Boolean(name.trim())}
      onSubmit={submit}
      onClose={onClose}
    >
      <Input label="Название" value={name} onChange={(event) => setName(event.target.value)} required />
      <SelectField
        label="Входит в"
        value={parentId}
        placeholder="Корневое место"
        options={activeLocationOptions(lookups, location?.id)}
        onChange={setParentId}
      />
    </FormDialog>
  );
}

// --- quantity operations (Slice 3) -----------------------------------------------

function stockOptions(stock: readonly InventoryStock[], lookups: InventoryLookups, unit: string): Option[] {
  return stock
    .map((row) => ({
      value: row.storage_location_id,
      label: `${locationPath(row.storage_location_id, lookups.locations)} — ${formatQuantity(row.quantity, unit)}`,
    }))
    .sort(byLabel);
}

function QuantityInput({ value, onChange, unit }: { value: string; onChange: (value: string) => void; unit: string }) {
  return (
    <Input
      label={unit ? `Количество, ${unit}` : "Количество"}
      value={value}
      inputMode="numeric"
      onChange={(event) => onChange(event.target.value)}
      hint="Целое число больше нуля."
      required
    />
  );
}

export function ReceiptDialog({
  item,
  lookups,
  onClose,
}: {
  item: InventoryItem;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [cost, setCost] = useState("");
  const [comment, setComment] = useState("");
  const receive = useReceiveInventoryQuantity();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const unit = unitName(item, lookups);
  const parsedQuantity = parseQuantity(quantity);
  const costMinor = parseRublesToMinor(cost);
  const costInvalid = Number.isNaN(costMinor);

  return (
    <FormDialog
      title="Принять на склад"
      description={item.name}
      submitLabel="Принять"
      pending={receive.isPending}
      error={error}
      canSubmit={Boolean(locationId && parsedQuantity) && !costInvalid}
      onSubmit={() => {
        setError(null);
        receive.mutate(
          {
            itemId: item.id,
            storage_location_id: locationId,
            quantity: parsedQuantity ?? 0,
            unit_cost_minor: costMinor,
            comment: blankToNull(comment),
          },
          handlers("Поступление принято"),
        );
      }}
      onClose={onClose}
    >
      <SelectField
        label="Место хранения"
        value={locationId}
        placeholder="Выберите место"
        options={activeLocationOptions(lookups)}
        onChange={setLocationId}
      />
      <QuantityInput value={quantity} onChange={setQuantity} unit={unit} />
      <Input
        label="Стоимость за единицу, ₽"
        value={cost}
        inputMode="decimal"
        onChange={(event) => setCost(event.target.value)}
        hint={costInvalid ? "Введите сумму в рублях, например 1250 или 1250,50." : "Необязательно."}
        aria-invalid={costInvalid}
      />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

export function TransferQuantityDialog({
  item,
  stock,
  fromLocationId = "",
  lookups,
  onClose,
}: {
  item: InventoryItem;
  stock: readonly InventoryStock[];
  fromLocationId?: string;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [fromId, setFromId] = useState(fromLocationId);
  const [toId, setToId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [comment, setComment] = useState("");
  const transfer = useTransferInventoryQuantity();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const unit = unitName(item, lookups);
  const parsedQuantity = parseQuantity(quantity);

  return (
    <FormDialog
      title="Переместить"
      description={item.name}
      submitLabel="Переместить"
      pending={transfer.isPending}
      error={error}
      canSubmit={Boolean(fromId && toId && parsedQuantity)}
      onSubmit={() => {
        setError(null);
        transfer.mutate(
          {
            itemId: item.id,
            from_location_id: fromId,
            to_location_id: toId,
            quantity: parsedQuantity ?? 0,
            comment: blankToNull(comment),
          },
          handlers("Перемещение выполнено"),
        );
      }}
      onClose={onClose}
    >
      <SelectField
        label="Откуда"
        value={fromId}
        placeholder="Выберите место с остатком"
        options={stockOptions(stock, lookups, unit)}
        onChange={setFromId}
      />
      <SelectField
        label="Куда"
        value={toId}
        placeholder="Выберите место"
        options={activeLocationOptions(lookups, fromId)}
        onChange={setToId}
      />
      <QuantityInput value={quantity} onChange={setQuantity} unit={unit} />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

export function WriteOffQuantityDialog({
  item,
  stock,
  locationId: initialLocationId = "",
  lookups,
  onClose,
}: {
  item: InventoryItem;
  stock: readonly InventoryStock[];
  locationId?: string;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState(initialLocationId);
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const writeOff = useWriteOffInventoryQuantity();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const unit = unitName(item, lookups);
  const parsedQuantity = parseQuantity(quantity);

  return (
    <FormDialog
      title="Списать"
      description={item.name}
      submitLabel="Списать"
      destructive
      pending={writeOff.isPending}
      error={error}
      canSubmit={Boolean(locationId && parsedQuantity && reason.trim())}
      onSubmit={() => {
        setError(null);
        writeOff.mutate(
          {
            itemId: item.id,
            storage_location_id: locationId,
            quantity: parsedQuantity ?? 0,
            comment: reason.trim(),
          },
          handlers("Списание оформлено"),
        );
      }}
      onClose={onClose}
    >
      <SelectField
        label="Место хранения"
        value={locationId}
        placeholder="Выберите место с остатком"
        options={stockOptions(stock, lookups, unit)}
        onChange={setLocationId}
      />
      <QuantityInput value={quantity} onChange={setQuantity} unit={unit} />
      <TextAreaField label="Причина списания" value={reason} onChange={setReason} hint="Обязательно." />
    </FormDialog>
  );
}

/** «Отменить списание»: the whole write-off is reversed (§16). The
 * original location is used by the backend; only when it answers that
 * the location is archived does the dialog ask for another one. */
export function ReverseWriteOffDialog({
  movement,
  accountingMode,
  lookups,
  onClose,
}: {
  movement: InventoryMovement;
  accountingMode: AccountingMode;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [needsLocation, setNeedsLocation] = useState(false);
  const [locationId, setLocationId] = useState("");
  const reverse = useReverseInventoryWriteOff();
  const notify = useNotify();
  const [error, setError] = useState<string | null>(null);

  function submit() {
    setError(null);
    reverse.mutate(
      { movement, accountingMode, storageLocationId: needsLocation ? locationId : undefined },
      {
        onSuccess: () => {
          notify("success", "Списание отменено");
          onClose();
        },
        onError: (failure: ApiError | Error) => {
          if ("code" in failure && failure.code === "storage_location_required") setNeedsLocation(true);
          setError("status" in failure ? inventoryErrorMessage(failure) : mutationErrorMessage(failure));
        },
      },
    );
  }

  return (
    <FormDialog
      title="Отменить списание?"
      description="Списание отменяется целиком: имущество вернётся в место, из которого его списали."
      submitLabel="Отменить списание"
      pending={reverse.isPending}
      error={error}
      canSubmit={!needsLocation || Boolean(locationId)}
      onSubmit={submit}
      onClose={onClose}
    >
      {needsLocation ? (
        <SelectField
          label="Место хранения"
          value={locationId}
          placeholder="Выберите место"
          options={activeLocationOptions(lookups)}
          onChange={setLocationId}
        />
      ) : null}
    </FormDialog>
  );
}

// --- instance operations (Slice 2) -----------------------------------------------

export function InstanceCreateDialog({
  item,
  lookups,
  onClose,
}: {
  item: InventoryItem;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [locationId, setLocationId] = useState("");
  const [cost, setCost] = useState("");
  const [barcode, setBarcode] = useState("");
  const [serial, setSerial] = useState("");
  const [description, setDescription] = useState("");
  const create = useCreateInventoryInstance();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const costMinor = parseRublesToMinor(cost);
  const costInvalid = Number.isNaN(costMinor);

  return (
    <FormDialog
      title="Принять экземпляр"
      description={`${item.name}. Inventory ID присвоит система.`}
      submitLabel="Принять"
      pending={create.isPending}
      error={error}
      canSubmit={Boolean(locationId) && !costInvalid}
      onSubmit={() => {
        setError(null);
        create.mutate(
          {
            item_id: item.id,
            storage_location_id: locationId,
            unit_cost_minor: costMinor,
            manufacturer_barcode: blankToNull(barcode),
            manufacturer_serial_number: blankToNull(serial),
            description: blankToNull(description),
          },
          handlers("Экземпляр принят на склад"),
        );
      }}
      onClose={onClose}
    >
      <SelectField
        label="Место хранения"
        value={locationId}
        placeholder="Выберите место"
        options={activeLocationOptions(lookups)}
        onChange={setLocationId}
      />
      <Input
        label="Стоимость поступления, ₽"
        value={cost}
        inputMode="decimal"
        onChange={(event) => setCost(event.target.value)}
        hint={costInvalid ? "Введите сумму в рублях, например 1250 или 1250,50." : "Необязательно."}
        aria-invalid={costInvalid}
      />
      <Input label="Штрихкод производителя" value={barcode} onChange={(event) => setBarcode(event.target.value)} />
      <Input label="Серийный номер" value={serial} onChange={(event) => setSerial(event.target.value)} />
      <TextAreaField label="Описание" value={description} onChange={setDescription} />
    </FormDialog>
  );
}

/** Only the three directly editable fields (§7.5); state and location
 * change through operations only. */
export function InstanceEditDialog({ instance, onClose }: { instance: InventoryInstance; onClose: () => void }) {
  const [barcode, setBarcode] = useState(instance.manufacturer_barcode ?? "");
  const [serial, setSerial] = useState(instance.manufacturer_serial_number ?? "");
  const [description, setDescription] = useState(instance.description ?? "");
  const update = useUpdateInventoryInstance();
  const { error, setError, handlers } = useDialogSubmit(onClose);

  return (
    <FormDialog
      title="Изменить экземпляр"
      description={instance.inventory_number}
      submitLabel="Сохранить"
      pending={update.isPending}
      error={error}
      onSubmit={() => {
        setError(null);
        update.mutate(
          {
            id: instance.id,
            fields: {
              manufacturer_barcode: blankToNull(barcode),
              manufacturer_serial_number: blankToNull(serial),
              description: blankToNull(description),
            },
          },
          handlers("Экземпляр сохранён"),
        );
      }}
      onClose={onClose}
    >
      <Input label="Штрихкод производителя" value={barcode} onChange={(event) => setBarcode(event.target.value)} />
      <Input label="Серийный номер" value={serial} onChange={(event) => setSerial(event.target.value)} />
      <TextAreaField label="Описание" value={description} onChange={setDescription} />
    </FormDialog>
  );
}

export function InstanceTransferDialog({
  instance,
  lookups,
  onClose,
}: {
  instance: InventoryInstance;
  lookups: InventoryLookups;
  onClose: () => void;
}) {
  const [toId, setToId] = useState("");
  const [comment, setComment] = useState("");
  const transfer = useTransferInventoryInstance();
  const { error, setError, handlers } = useDialogSubmit(onClose);

  return (
    <FormDialog
      title="Переместить экземпляр"
      description={`${instance.inventory_number}: ${locationPath(instance.storage_location_id, lookups.locations)}`}
      submitLabel="Переместить"
      pending={transfer.isPending}
      error={error}
      canSubmit={Boolean(toId)}
      onSubmit={() => {
        setError(null);
        transfer.mutate(
          { id: instance.id, to_location_id: toId, comment: blankToNull(comment) },
          handlers("Экземпляр перемещён"),
        );
      }}
      onClose={onClose}
    >
      <SelectField
        label="Куда"
        value={toId}
        placeholder="Выберите место"
        options={activeLocationOptions(lookups, instance.storage_location_id ?? undefined)}
        onChange={setToId}
      />
      <TextAreaField label="Комментарий" value={comment} onChange={setComment} />
    </FormDialog>
  );
}

export function InstanceRepairDialog({
  instance,
  action,
  onClose,
}: {
  instance: InventoryInstance;
  action: "repair-start" | "repair-end";
  onClose: () => void;
}) {
  const repair = useInstanceRepair();
  const { error, setError, handlers } = useDialogSubmit(onClose);
  const start = action === "repair-start";

  return (
    <FormDialog
      title={start ? "Отправить в ремонт?" : "Завершить ремонт?"}
      description={
        start
          ? `${instance.inventory_number} останется в своём месте хранения.`
          : `${instance.inventory_number} снова будет в наличии.`
      }
      submitLabel={start ? "Начать ремонт" : "Завершить ремонт"}
      pending={repair.isPending}
      error={error}
      onSubmit={() => {
        setError(null);
        repair.mutate({ id: instance.id, action }, handlers(start ? "Ремонт начат" : "Ремонт завершён"));
      }}
      onClose={onClose}
    />
  );
}

export function InstanceWriteOffDialog({ instance, onClose }: { instance: InventoryInstance; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const writeOff = useWriteOffInventoryInstance();
  const { error, setError, handlers } = useDialogSubmit(onClose);

  return (
    <FormDialog
      title="Списать экземпляр"
      description={instance.inventory_number}
      submitLabel="Списать"
      destructive
      pending={writeOff.isPending}
      error={error}
      canSubmit={Boolean(reason.trim())}
      onSubmit={() => {
        setError(null);
        writeOff.mutate({ id: instance.id, comment: reason.trim() }, handlers("Экземпляр списан"));
      }}
      onClose={onClose}
    >
      <TextAreaField label="Причина списания" value={reason} onChange={setReason} hint="Обязательно." />
    </FormDialog>
  );
}
