import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  useInventoryItems,
  useInventoryStock,
  useInventoryStorageLocations,
  type InstanceState,
  type InventoryStatusFilter,
} from "../api/inventory";
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
  locationPath,
  recordStatusIcon,
  recordStatusLabel,
  type LocationTreeNode,
} from "../domain/inventoryFormat";
import { unitName, useInventoryLookups, type InventoryLookups } from "../hooks/useInventoryLookups";
import { InstanceList, InventoryQueryError, MetaList } from "./InventoryShared";
import styles from "./Inventory.module.css";

const TABS = ["items", "locations", "stock", "instances"] as const;
type TabId = (typeof TABS)[number];

const STATUS_OPTIONS: FilterOption[] = [
  { value: "active", label: "Активные" },
  { value: "archived", label: "В архиве" },
  { value: "all", label: "Все" },
];

function isTabId(value: string | null): value is TabId {
  return TABS.includes(value as TabId);
}

/** «Склад» (docs/04-domain/inventory.md, Administrator only): a
 * read-only overview of the existing inventory backend — items, storage
 * locations, quantity stock and instances. The active tab lives in the
 * URL so Back from an item returns to the same tab. */
export function InventoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const activeTab: TabId = isTabId(tabParam) ? tabParam : "items";
  const { lookups, isLoading, error, refetch } = useInventoryLookups();

  function selectTab(id: string) {
    setSearchParams(id === "items" ? {} : { tab: id }, { replace: true });
  }

  return (
    <div className={styles.page}>
      <PageHeader title="Склад" description="Номенклатура, места хранения, остатки и экземпляры клуба." />
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
            { id: "locations", label: "Места хранения", content: <LocationsTab /> },
            { id: "stock", label: "Остатки", content: <StockTab lookups={lookups} /> },
            { id: "instances", label: "Экземпляры", content: <InstancesTab lookups={lookups} /> },
          ]}
        />
      ) : null}
    </div>
  );
}

function ItemsTab({ lookups }: { lookups: InventoryLookups }) {
  const [status, setStatus] = useState<InventoryStatusFilter>("active");
  const [page, setPage] = useState(1);
  const query = useInventoryItems({ status, page });

  return (
    <div>
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
          illustration={status === "active" ? "empty-groups" : "no-results"}
          title={status === "active" ? "Номенклатуры пока нет" : "Ничего не найдено"}
          description={
            status === "active"
              ? "Здесь появятся позиции склада."
              : "Попробуйте выбрать другой статус."
          }
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
                      <Link to={`/inventory/items/${item.id}`} className={styles.rowTitleLink}>
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
    </div>
  );
}

function LocationsTab() {
  const [status, setStatus] = useState<InventoryStatusFilter>("active");
  const query = useInventoryStorageLocations(status);

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
      {query.isLoading ? <Loading label="Загружаем места хранения…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить места хранения"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState
          illustration={status === "active" ? "empty-groups" : "no-results"}
          title={status === "active" ? "Мест хранения пока нет" : "Ничего не найдено"}
          description={
            status === "active"
              ? "Здесь появятся склады, стеллажи и полки клуба."
              : "Попробуйте выбрать другой статус."
          }
        />
      ) : null}
      {query.isSuccess && query.data.length > 0 ? (
        <LocationTree nodes={buildLocationTree(query.data)} label="Места хранения" />
      ) : null}
    </div>
  );
}

function LocationTree({ nodes, label }: { nodes: LocationTreeNode[]; label?: string }) {
  return (
    <ul className={styles.tree} aria-label={label}>
      {nodes.map((node) => (
        <li key={node.location.id} className={styles.treeItem}>
          <div className={styles.treeRow}>
            <span>{node.location.name}</span>
            {node.location.status === "archived" ? (
              <StatusBadge status={recordStatusIcon("archived")} label={recordStatusLabel("archived")} />
            ) : null}
          </div>
          {node.children.length > 0 ? <LocationTree nodes={node.children} /> : null}
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

function StockTab({ lookups }: { lookups: InventoryLookups }) {
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
          illustration={filtered ? "no-results" : "empty-groups"}
          title={filtered ? "Ничего не найдено" : "Остатков пока нет"}
          description={
            filtered
              ? "Попробуйте изменить фильтры."
              : "Здесь появятся остатки позиций с количественным учётом."
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
                        <Link to={`/inventory/items/${row.item_id}`} className={styles.rowTitleLink}>
                          {item?.name ?? "Позиция"}
                        </Link>
                      </h3>
                      <span className={styles.quantity}>
                        {`${row.quantity} ${unitName(item, lookups)}`.trim()}
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
