import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  useInventoryItems,
  useInventoryStock,
  type InstanceState,
  type InventoryCategory,
  type InventoryItem,
  type InventoryStatusFilter,
  type InventoryStock,
  type InventoryStorageLocation,
  type InventoryUnit,
} from "../api/inventory";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { FilterSelect, type FilterOption } from "../components/ui/FilterSelect";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Tabs } from "../components/ui/Tabs";
import {
  INSTANCE_STATE_OPTIONS,
  accountingModeLabel,
  buildLocationTree,
  formatCostMinor,
  formatQuantity,
  isInventoryTab,
  locationPath,
  type InventoryTab,
  type LocationTreeNode,
} from "../domain/inventoryFormat";
import { recordStatusIcon, recordStatusLabel } from "../domain/statusMapping";
import { unitName, useInventoryLookups, type InventoryLookups } from "../hooks/useInventoryLookups";
import {
  ArchiveDialog,
  ItemFormDialog,
  LocationFormDialog,
  ReferenceNameDialog,
  TransferQuantityDialog,
  WriteOffQuantityDialog,
  type ArchiveTarget,
} from "./InventoryForms";
import { IssuesTab } from "./InventoryIssues";
import { InstanceList, InventoryQueryError, MetaList } from "./InventoryShared";
import styles from "./Inventory.module.css";

const STATUS_OPTIONS: FilterOption[] = [
  { value: "active", label: "Активные" },
  { value: "archived", label: "В архиве" },
  { value: "all", label: "Все" },
];

/** «Склад» (docs/04-domain/inventory.md, Administrator only): the
 * Administrator's warehouse — items, storage locations, quantity stock,
 * instances, issues and the category/unit reference data, each with its
 * contextual operations. The active tab lives in the URL so Back from an
 * item returns to the same tab; links to an item also carry it as router
 * state for the item page's «← Склад» link. */
export function InventoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const activeTab: InventoryTab = isInventoryTab(tabParam) ? tabParam : "items";
  const { lookups, isLoading, error, refetch } = useInventoryLookups();

  function selectTab(id: string) {
    setSearchParams(id === "items" ? {} : { tab: id }, { replace: true });
  }

  return (
    <div className={styles.page}>
      <PageHeader
        title="Склад"
        description="Номенклатура, места хранения, остатки, экземпляры и выдачи имущества клуба."
      />
      {isLoading ? <Loading label="Загружаем склад…" /> : null}
      {error ? (
        <InventoryQueryError error={error} title="Не удалось загрузить склад" onRetry={refetch} />
      ) : null}
      {!isLoading && !error ? (
        <Tabs
          label="Разделы склада"
          activeId={activeTab}
          onChange={selectTab}
          items={[
            { id: "items", label: "Номенклатура", content: <ItemsTab lookups={lookups} /> },
            { id: "locations", label: "Места хранения", content: <LocationsTab lookups={lookups} /> },
            { id: "stock", label: "Остатки", content: <StockTab lookups={lookups} /> },
            { id: "instances", label: "Экземпляры", content: <InstancesTab lookups={lookups} /> },
            { id: "issues", label: "Выдачи", content: <IssuesTab lookups={lookups} /> },
            { id: "references", label: "Справочники", content: <ReferencesTab lookups={lookups} /> },
          ]}
        />
      ) : null}
    </div>
  );
}

function ItemsTab({ lookups }: { lookups: InventoryLookups }) {
  const [status, setStatus] = useState<InventoryStatusFilter>("active");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<InventoryItem | "new" | null>(null);
  const [archiving, setArchiving] = useState<ArchiveTarget | null>(null);
  const query = useInventoryItems({ status, page });
  const createButton = (
    <Button variant="primary" icon="action.add" onClick={() => setEditing("new")}>
      Создать номенклатуру
    </Button>
  );

  return (
    <div>
      <div className={styles.sectionHeader}>
        <p className={styles.muted}>Виды имущества склада и режим их учёта.</p>
        {createButton}
      </div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Статус"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => {
            setStatus(value as InventoryStatusFilter);
            setPage(1);
          }}
        />
      </div>
      {query.isLoading ? <Loading label="Загружаем номенклатуру…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить номенклатуру"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState
          illustration={status === "active" ? "empty-inventory" : "no-results"}
          title={status === "active" ? "Номенклатуры пока нет" : "Ничего не найдено"}
          description={
            status === "active"
              ? "Создайте первую позицию, чтобы принимать имущество на склад."
              : "Попробуйте выбрать другой статус."
          }
          action={status === "active" ? createButton : undefined}
        />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <ul className={styles.list} aria-label="Номенклатура">
            {query.data.items.map((item) => (
              <li key={item.id}>
                <Card>
                  <div className={styles.rowHeader}>
                    <h3 className={styles.rowTitle}>
                      <Link
                        to={`/inventory/items/${item.id}`}
                        state={{ inventoryTab: "items" }}
                        className={styles.rowTitleLink}
                      >
                        {item.name}
                      </Link>
                    </h3>
                    <StatusBadge
                      status={recordStatusIcon(item.status)}
                      label={recordStatusLabel(item.status)}
                    />
                  </div>
                  <MetaList
                    entries={[
                      { label: "Режим учёта", value: accountingModeLabel(item.accounting_mode) },
                      { label: "Категория", value: lookups.categories.get(item.category_id)?.name ?? "—" },
                      { label: "Единица", value: unitName(item, lookups) || "—" },
                      { label: "Стоимость", value: formatCostMinor(item.current_cost_minor) },
                    ]}
                  />
                  {item.status === "active" ? (
                    <div className={styles.cardActions}>
                      <Button variant="secondary" icon="action.edit" onClick={() => setEditing(item)}>
                        Изменить
                      </Button>
                      <Button
                        variant="secondary"
                        icon="action.archive"
                        onClick={() => setArchiving({ kind: "item", record: item })}
                      >
                        Архивировать
                      </Button>
                    </div>
                  ) : null}
                </Card>
              </li>
            ))}
          </ul>
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}
      {editing ? (
        <ItemFormDialog
          item={editing === "new" ? undefined : editing}
          lookups={lookups}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {archiving ? <ArchiveDialog target={archiving} onClose={() => setArchiving(null)} /> : null}
    </div>
  );
}

type LocationEdit = { location?: InventoryStorageLocation; parentId?: string };

type LocationActions = {
  onAddChild: (parent: InventoryStorageLocation) => void;
  onEdit: (location: InventoryStorageLocation) => void;
  onArchive: (location: InventoryStorageLocation) => void;
};

function LocationsTab({ lookups }: { lookups: InventoryLookups }) {
  const [status, setStatus] = useState<InventoryStatusFilter>("active");
  const [editing, setEditing] = useState<LocationEdit | null>(null);
  const [archiving, setArchiving] = useState<ArchiveTarget | null>(null);
  const actions: LocationActions = {
    onAddChild: (parent) => setEditing({ parentId: parent.id }),
    onEdit: (location) => setEditing({ location }),
    onArchive: (location) => setArchiving({ kind: "location", record: location }),
  };
  const createButton = (
    <Button variant="primary" icon="action.add" onClick={() => setEditing({})}>
      Создать место
    </Button>
  );
  // The lookups already hold every location (active and archived); the
  // status filter narrows that one list instead of a second request.
  const locations = [...lookups.locations.values()].filter(
    (location) => status === "all" || location.status === status,
  );

  return (
    <div>
      <div className={styles.sectionHeader}>
        <p className={styles.muted}>Склады, стеллажи, полки и ячейки — иерархия любой глубины.</p>
        {createButton}
      </div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Статус"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setStatus(value as InventoryStatusFilter)}
        />
      </div>
      {locations.length === 0 ? (
        <EmptyState
          illustration={status === "active" ? "empty-inventory" : "no-results"}
          title={status === "active" ? "Мест хранения пока нет" : "Ничего не найдено"}
          description={
            status === "active"
              ? "Создайте первое место, чтобы принимать имущество."
              : "Попробуйте выбрать другой статус."
          }
          action={status === "active" ? createButton : undefined}
        />
      ) : (
        <LocationTree
          nodes={buildLocationTree(locations)}
          lookups={lookups}
          actions={actions}
          label="Места хранения"
          topLevel
        />
      )}
      {editing ? (
        <LocationFormDialog
          location={editing.location}
          parentId={editing.parentId}
          lookups={lookups}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {archiving ? <ArchiveDialog target={archiving} onClose={() => setArchiving(null)} /> : null}
    </div>
  );
}

type LocationTreeProps = {
  nodes: LocationTreeNode[];
  lookups: InventoryLookups;
  actions: LocationActions;
  label?: string;
  /** The tree's own roots (not a nested child list). */
  topLevel?: boolean;
};

/** A top-level node whose parent is outside the current selection (e.g.
 * an archived shelf of an active rack under «В архиве») shows its
 * parent's full path, so its place in the hierarchy is never lost. */
function LocationTree({ nodes, lookups, actions, label, topLevel = false }: LocationTreeProps) {
  return (
    <ul className={styles.tree} aria-label={label}>
      {nodes.map((node) => (
        <li key={node.location.id} className={styles.treeItem}>
          <div className={styles.treeRow}>
            <span>{node.location.name}</span>
            {node.location.status === "archived" ? (
              <StatusBadge status={recordStatusIcon("archived")} label={recordStatusLabel("archived")} />
            ) : null}
            {node.location.status === "active" ? (
              <span className={styles.treeActions}>
                <Button
                  variant="secondary"
                  icon="action.add"
                  aria-label={`Вложенное место в «${node.location.name}»`}
                  onClick={() => actions.onAddChild(node.location)}
                >
                  Вложить
                </Button>
                <Button
                  variant="secondary"
                  icon="action.edit"
                  aria-label={`Изменить «${node.location.name}»`}
                  onClick={() => actions.onEdit(node.location)}
                >
                  Изменить
                </Button>
                <Button
                  variant="secondary"
                  icon="action.archive"
                  aria-label={`Архивировать «${node.location.name}»`}
                  onClick={() => actions.onArchive(node.location)}
                >
                  В архив
                </Button>
              </span>
            ) : null}
            {topLevel && node.location.parent_id ? (
              <span className={styles.treeContext}>
                {`Входит в: ${locationPath(node.location.parent_id, lookups.locations)}`}
              </span>
            ) : null}
          </div>
          {node.children.length > 0 ? (
            <LocationTree nodes={node.children} lookups={lookups} actions={actions} />
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function sortedOptions(entries: Array<{ value: string; label: string }>, allLabel: string): FilterOption[] {
  return [
    { value: "", label: allLabel },
    ...entries.sort((a, b) => a.label.localeCompare(b.label, "ru")),
  ];
}

function locationOptions(lookups: InventoryLookups, allLabel: string): FilterOption[] {
  return sortedOptions(
    [...lookups.locations.values()].map((location) => ({
      value: location.id,
      label: locationPath(location.id, lookups.locations),
    })),
    allLabel,
  );
}

type StockOperation = { kind: "transfer" | "write-off"; item: InventoryItem; row: InventoryStock };

function StockTab({ lookups }: { lookups: InventoryLookups }) {
  const [operation, setOperation] = useState<StockOperation | null>(null);
  const [itemId, setItemId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [page, setPage] = useState(1);
  const query = useInventoryStock({ itemId, locationId, page });
  const filtered = Boolean(itemId || locationId);

  const itemOptions = sortedOptions(
    [...lookups.items.values()]
      .filter((item) => item.accounting_mode === "quantity")
      .map((item) => ({ value: item.id, label: item.name })),
    "Вся номенклатура",
  );

  return (
    <div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Номенклатура"
          value={itemId}
          options={itemOptions}
          onChange={(value) => {
            setItemId(value);
            setPage(1);
          }}
        />
        <FilterSelect
          label="Место хранения"
          value={locationId}
          options={locationOptions(lookups, "Все места")}
          onChange={(value) => {
            setLocationId(value);
            setPage(1);
          }}
        />
      </div>
      {query.isLoading ? <Loading label="Загружаем остатки…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить остатки"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState
          illustration={filtered ? "no-results" : "empty-inventory"}
          title={filtered ? "Ничего не найдено" : "Остатков пока нет"}
          description={
            filtered
              ? "Попробуйте изменить фильтры."
              : "Остатки появятся после приёма имущества: откройте позицию с количественным учётом и нажмите «Принять на склад»."
          }
        />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <ul className={styles.list} aria-label="Остатки">
            {query.data.items.map((row) => {
              const item = lookups.items.get(row.item_id);
              return (
                <li key={`${row.item_id}:${row.storage_location_id}`}>
                  <Card>
                    <div className={styles.rowHeader}>
                      <h3 className={styles.rowTitle}>
                        <Link
                          to={`/inventory/items/${row.item_id}`}
                          state={{ inventoryTab: "stock" }}
                          className={styles.rowTitleLink}
                        >
                          {item?.name ?? "Позиция"}
                        </Link>
                      </h3>
                      <span className={styles.quantity}>
                        {formatQuantity(row.quantity, unitName(item, lookups))}
                      </span>
                    </div>
                    <MetaList
                      entries={[
                        {
                          label: "Место хранения",
                          value: locationPath(row.storage_location_id, lookups.locations),
                        },
                      ]}
                    />
                    {item?.status === "active" ? (
                      <div className={styles.cardActions}>
                        <Button
                          variant="secondary"
                          onClick={() => setOperation({ kind: "transfer", item, row })}
                        >
                          Переместить
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => setOperation({ kind: "write-off", item, row })}
                        >
                          Списать
                        </Button>
                      </div>
                    ) : null}
                  </Card>
                </li>
              );
            })}
          </ul>
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}
      {operation?.kind === "transfer" ? (
        <TransferQuantityDialog
          item={operation.item}
          stock={[operation.row]}
          fromLocationId={operation.row.storage_location_id}
          lookups={lookups}
          onClose={() => setOperation(null)}
        />
      ) : null}
      {operation?.kind === "write-off" ? (
        <WriteOffQuantityDialog
          item={operation.item}
          stock={[operation.row]}
          locationId={operation.row.storage_location_id}
          lookups={lookups}
          onClose={() => setOperation(null)}
        />
      ) : null}
    </div>
  );
}

function InstancesTab({ lookups }: { lookups: InventoryLookups }) {
  const [itemId, setItemId] = useState("");
  const [state, setState] = useState<InstanceState | "">("");
  const [locationId, setLocationId] = useState("");
  const [page, setPage] = useState(1);
  const filtered = Boolean(itemId || state || locationId);

  const itemOptions = sortedOptions(
    [...lookups.items.values()]
      .filter((item) => item.accounting_mode === "instance")
      .map((item) => ({ value: item.id, label: item.name })),
    "Вся номенклатура",
  );

  return (
    <div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Номенклатура"
          value={itemId}
          options={itemOptions}
          onChange={(value) => {
            setItemId(value);
            setPage(1);
          }}
        />
        <FilterSelect
          label="Состояние"
          value={state}
          options={INSTANCE_STATE_OPTIONS}
          onChange={(value) => {
            setState(value as InstanceState | "");
            setPage(1);
          }}
        />
        <FilterSelect
          label="Место хранения"
          value={locationId}
          options={locationOptions(lookups, "Все места")}
          onChange={(value) => {
            setLocationId(value);
            setPage(1);
          }}
        />
      </div>
      <InstanceList
        lookups={lookups}
        filters={{ itemId, state, locationId, page }}
        filtered={filtered}
        showItem
        onPageChange={setPage}
      />
    </div>
  );
}

// --- categories and units ------------------------------------------------------

type ReferenceEdit =
  | { kind: "category"; record?: InventoryCategory }
  | { kind: "unit"; record?: InventoryUnit };

/** Category and unit reference data used by nomenclature (§8, §9). System
 * units are read-only; archiving is irreversible. */
function ReferencesTab({ lookups }: { lookups: InventoryLookups }) {
  const [status, setStatus] = useState<InventoryStatusFilter>("active");
  const [editing, setEditing] = useState<ReferenceEdit | null>(null);
  const [archiving, setArchiving] = useState<ArchiveTarget | null>(null);
  const visible = <T extends { status: string; name: string }>(records: Iterable<T>) =>
    [...records]
      .filter((record) => status === "all" || record.status === status)
      .sort((a, b) => a.name.localeCompare(b.name, "ru"));
  const categories = visible(lookups.categories.values());
  const units = visible(lookups.units.values());

  return (
    <div>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Статус"
          value={status}
          options={STATUS_OPTIONS}
          onChange={(value) => setStatus(value as InventoryStatusFilter)}
        />
      </div>
      <section className={styles.section} aria-labelledby="inventory-categories">
        <div className={styles.sectionHeader}>
          <h2 id="inventory-categories" className={styles.sectionTitle}>
            Категории
          </h2>
          <Button variant="primary" icon="action.add" onClick={() => setEditing({ kind: "category" })}>
            Создать категорию
          </Button>
        </div>
        {categories.length === 0 ? (
          <EmptyState
            illustration={status === "active" ? "empty-inventory" : "no-results"}
            title={status === "active" ? "Категорий пока нет" : "Ничего не найдено"}
            description={status === "active" ? "Категории нужны для создания номенклатуры." : undefined}
          />
        ) : (
          <ul className={styles.list} aria-label="Категории">
            {categories.map((category) => (
              <li key={category.id}>
                <ReferenceRow
                  name={category.name}
                  status={category.status}
                  editable={category.status === "active"}
                  onEdit={() => setEditing({ kind: "category", record: category })}
                  onArchive={() => setArchiving({ kind: "category", record: category })}
                />
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className={styles.section} aria-labelledby="inventory-units">
        <div className={styles.sectionHeader}>
          <h2 id="inventory-units" className={styles.sectionTitle}>
            Единицы измерения
          </h2>
          <Button variant="primary" icon="action.add" onClick={() => setEditing({ kind: "unit" })}>
            Создать единицу
          </Button>
        </div>
        {units.length === 0 ? (
          <EmptyState illustration="no-results" title="Ничего не найдено" />
        ) : (
          <ul className={styles.list} aria-label="Единицы измерения">
            {units.map((unit) => (
              <li key={unit.id}>
                <ReferenceRow
                  name={unit.name}
                  status={unit.status}
                  note={unit.is_system ? "Системная единица — только чтение" : undefined}
                  editable={unit.status === "active" && !unit.is_system}
                  onEdit={() => setEditing({ kind: "unit", record: unit })}
                  onArchive={() => setArchiving({ kind: "unit", record: unit })}
                />
              </li>
            ))}
          </ul>
        )}
      </section>
      {editing ? (
        <ReferenceNameDialog kind={editing.kind} record={editing.record} onClose={() => setEditing(null)} />
      ) : null}
      {archiving ? <ArchiveDialog target={archiving} onClose={() => setArchiving(null)} /> : null}
    </div>
  );
}

function ReferenceRow({
  name,
  status,
  note,
  editable,
  onEdit,
  onArchive,
}: {
  name: string;
  status: "active" | "archived";
  note?: string;
  editable: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  return (
    <Card>
      <div className={styles.rowHeader}>
        <h3 className={styles.rowTitle}>{name}</h3>
        <StatusBadge status={recordStatusIcon(status)} label={recordStatusLabel(status)} />
      </div>
      {note ? <p className={styles.muted}>{note}</p> : null}
      {editable ? (
        <div className={styles.cardActions}>
          <Button variant="secondary" icon="action.edit" aria-label={`Переименовать «${name}»`} onClick={onEdit}>
            Изменить
          </Button>
          <Button variant="secondary" icon="action.archive" aria-label={`Архивировать «${name}»`} onClick={onArchive}>
            Архивировать
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
