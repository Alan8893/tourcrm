import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useInventoryItem,
  useInventoryItemMovements,
  useInventoryItemStock,
  type InstanceState,
  type InventoryItem,
} from "../api/inventory";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { FilterSelect } from "../components/ui/FilterSelect";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import {
  INSTANCE_STATE_OPTIONS,
  accountingModeLabel,
  formatCostMinor,
  locationPath,
  recordStatusIcon,
  recordStatusLabel,
} from "../domain/inventoryFormat";
import { unitName, useInventoryLookups, type InventoryLookups } from "../hooks/useInventoryLookups";
import { InstanceList, InventoryQueryError, MetaList, MovementList } from "./InventoryShared";
import styles from "./Inventory.module.css";

const BACK = { to: "/inventory", label: "Склад" };

/** One inventory item: its card, then — by accounting mode — its stock
 * by location and movement history (quantity) or its instances
 * (instance; each instance has its own history page). */
export function InventoryItemPage() {
  const { itemId } = useParams<{ itemId: string }>();
  const itemQuery = useInventoryItem(itemId);
  const { lookups, isLoading, error, refetch } = useInventoryLookups();

  if (itemQuery.isLoading || isLoading) {
    return (
      <div>
        <PageHeader title="Позиция склада" back={BACK} />
        <Loading label="Загружаем позицию…" />
      </div>
    );
  }
  const failure = itemQuery.error ?? error;
  if (failure) {
    return (
      <div>
        <PageHeader title="Позиция склада" back={BACK} />
        <InventoryQueryError
          error={failure}
          title="Не удалось загрузить позицию"
          onRetry={() => {
            void itemQuery.refetch();
            refetch();
          }}
        />
      </div>
    );
  }
  const item = itemQuery.data;
  if (!item) return null;

  return (
    <div>
      <PageHeader
        title={item.name}
        back={BACK}
        titleExtra={
          <StatusBadge status={recordStatusIcon(item.status)} label={recordStatusLabel(item.status)} />
        }
      />
      <Card>
        <MetaList
          entries={[
            { label: "Режим учёта", value: accountingModeLabel(item.accounting_mode) },
            { label: "Категория", value: lookups.categories.get(item.category_id)?.name ?? "—" },
            { label: "Единица", value: unitName(item, lookups) || "—" },
            { label: "Стоимость", value: formatCostMinor(item.current_cost_minor) },
          ]}
        />
      </Card>
      {item.accounting_mode === "quantity" ? (
        <>
          <ItemStockSection item={item} lookups={lookups} />
          <ItemMovementsSection item={item} lookups={lookups} />
        </>
      ) : (
        <ItemInstancesSection item={item} lookups={lookups} />
      )}
    </div>
  );
}

type SectionProps = { item: InventoryItem; lookups: InventoryLookups };

function ItemStockSection({ item, lookups }: SectionProps) {
  const query = useInventoryItemStock(item.id);
  const unit = unitName(item, lookups);

  return (
    <section className={styles.section} aria-labelledby="inventory-item-stock">
      <h2 id="inventory-item-stock" className={styles.sectionTitle}>
        Остатки по местам хранения
      </h2>
      {query.isLoading ? <Loading label="Загружаем остатки…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить остатки"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState illustration="empty-groups" title="Остатка нет" description="Позиции нет ни в одном месте хранения." />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <ul className={styles.list} aria-label="Остатки по местам хранения">
          {query.data.items.map((row) => (
            <li key={row.storage_location_id}>
              <Card>
                <div className={styles.rowHeader}>
                  <span className={styles.rowTitle}>
                    {locationPath(row.storage_location_id, lookups.locations)}
                  </span>
                  <span className={styles.quantity}>{`${row.quantity} ${unit}`.trim()}</span>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function ItemMovementsSection({ item, lookups }: SectionProps) {
  const [page, setPage] = useState(1);
  const query = useInventoryItemMovements(item.id, page);

  return (
    <section className={styles.section} aria-labelledby="inventory-item-movements">
      <h2 id="inventory-item-movements" className={styles.sectionTitle}>
        История движений
      </h2>
      {query.isLoading ? <Loading label="Загружаем историю…" /> : null}
      {query.isError ? (
        <InventoryQueryError
          error={query.error}
          title="Не удалось загрузить историю"
          onRetry={() => void query.refetch()}
        />
      ) : null}
      {query.isSuccess && query.data.items.length === 0 ? (
        <EmptyState illustration="empty-groups" title="Движений пока нет" />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <MovementList movements={query.data.items} lookups={lookups} unit={unitName(item, lookups)} />
          <Pagination
            page={query.data.pagination.page}
            pages={query.data.pagination.pages}
            total={query.data.pagination.total}
            onPageChange={setPage}
          />
        </>
      ) : null}
    </section>
  );
}

function ItemInstancesSection({ item, lookups }: SectionProps) {
  const [state, setState] = useState<InstanceState | "">("");
  const [page, setPage] = useState(1);

  return (
    <section className={styles.section} aria-labelledby="inventory-item-instances">
      <h2 id="inventory-item-instances" className={styles.sectionTitle}>
        Экземпляры
      </h2>
      <div className={styles.toolbar}>
        <FilterSelect
          label="Состояние"
          value={state}
          options={INSTANCE_STATE_OPTIONS}
          onChange={(value) => {
            setState(value as InstanceState | "");
            setPage(1);
          }}
        />
      </div>
      <InstanceList
        lookups={lookups}
        filters={{ itemId: item.id, state, locationId: "", page }}
        filtered={Boolean(state)}
        showItem={false}
        onPageChange={setPage}
      />
    </section>
  );
}
