import { useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import {
  useInventoryItem,
  useInventoryItemMovements,
  useInventoryItemStock,
  type InstanceState,
  type InventoryItem,
  type InventoryMovement,
  type InventoryStock,
} from "../api/inventory";
import { Button } from "../components/ui/Button";
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
  formatQuantity,
  inventoryTabHref,
  isInventoryTab,
  locationPath,
} from "../domain/inventoryFormat";
import { recordStatusIcon, recordStatusLabel } from "../domain/statusMapping";
import { unitName, useInventoryLookups, type InventoryLookups } from "../hooks/useInventoryLookups";
import {
  ArchiveDialog,
  InstanceCreateDialog,
  ItemFormDialog,
  ReceiptDialog,
  ReverseWriteOffDialog,
  TransferQuantityDialog,
  WriteOffQuantityDialog,
} from "./InventoryForms";
import { InstanceList, InventoryQueryError, MetaList, MovementList } from "./InventoryShared";
import styles from "./Inventory.module.css";

type ItemOperation =
  | { kind: "edit" | "archive" | "receipt" | "instance" }
  | { kind: "transfer" | "write-off"; locationId?: string }
  | { kind: "reverse"; movement: InventoryMovement };

/** One inventory item: its card and operations, then — by accounting
 * mode — its stock by location and movement history (quantity) or its
 * instances (instance; each instance has its own history page). */
export function InventoryItemPage() {
  const [operation, setOperation] = useState<ItemOperation | null>(null);
  const { itemId } = useParams<{ itemId: string }>();
  // «← Склад» returns to the /inventory tab the item was opened from
  // (router state set by that tab's links); otherwise to «Номенклатура».
  const fromTab = (useLocation().state as { inventoryTab?: unknown } | null)?.inventoryTab;
  const back = { to: inventoryTabHref(isInventoryTab(fromTab) ? fromTab : "items"), label: "Склад" };
  const itemQuery = useInventoryItem(itemId);
  const { lookups, isLoading, error, refetch } = useInventoryLookups();

  if (itemQuery.isLoading || isLoading) {
    return (
      <div>
        <PageHeader title="Позиция склада" back={back} />
        <Loading label="Загружаем позицию…" />
      </div>
    );
  }
  const failure = itemQuery.error ?? error;
  if (failure) {
    return (
      <div>
        <PageHeader title="Позиция склада" back={back} />
        <InventoryQueryError
          error={failure}
          title="Не удалось загрузить позицию"
          pathParam="item_id"
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
  const active = item.status === "active";
  const close = () => setOperation(null);

  return (
    <div className={styles.page}>
      <PageHeader
        title={item.name}
        back={back}
        titleExtra={
          <StatusBadge status={recordStatusIcon(item.status)} label={recordStatusLabel(item.status)} />
        }
      />
      {active ? (
        <div className={`${styles.actions} ${styles.pageActions}`} role="group" aria-label="Операции с позицией">
          {item.accounting_mode === "quantity" ? (
            <>
              <Button variant="primary" icon="action.add" onClick={() => setOperation({ kind: "receipt" })}>
                Принять на склад
              </Button>
              <Button variant="secondary" onClick={() => setOperation({ kind: "transfer" })}>
                Переместить
              </Button>
              <Button variant="secondary" onClick={() => setOperation({ kind: "write-off" })}>
                Списать
              </Button>
            </>
          ) : (
            <Button variant="primary" icon="action.add" onClick={() => setOperation({ kind: "instance" })}>
              Принять экземпляр
            </Button>
          )}
          <Button variant="secondary" icon="action.edit" onClick={() => setOperation({ kind: "edit" })}>
            Изменить
          </Button>
          <Button variant="secondary" icon="action.archive" onClick={() => setOperation({ kind: "archive" })}>
            Архивировать
          </Button>
        </div>
      ) : null}
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
          <ItemStockSection item={item} lookups={lookups} onOperation={active ? setOperation : undefined} />
          <ItemMovementsSection item={item} lookups={lookups} onOperation={active ? setOperation : undefined} />
        </>
      ) : (
        <ItemInstancesSection item={item} lookups={lookups} />
      )}
      <ItemOperationDialog item={item} lookups={lookups} operation={operation} onClose={close} />
    </div>
  );
}

function ItemOperationDialog({
  item,
  lookups,
  operation,
  onClose,
}: {
  item: InventoryItem;
  lookups: InventoryLookups;
  operation: ItemOperation | null;
  onClose: () => void;
}) {
  // Transfer/write-off choose among the backend's stock rows of the item.
  const stock = useInventoryItemStock(
    operation?.kind === "transfer" || operation?.kind === "write-off" ? item.id : undefined,
  );
  if (!operation) return null;
  switch (operation.kind) {
    case "edit":
      return <ItemFormDialog item={item} lookups={lookups} onClose={onClose} />;
    case "archive":
      return <ArchiveDialog target={{ kind: "item", record: item }} onClose={onClose} />;
    case "receipt":
      return <ReceiptDialog item={item} lookups={lookups} onClose={onClose} />;
    case "instance":
      return <InstanceCreateDialog item={item} lookups={lookups} onClose={onClose} />;
    case "reverse":
      return (
        <ReverseWriteOffDialog
          movement={operation.movement}
          accountingMode={item.accounting_mode}
          lookups={lookups}
          onClose={onClose}
        />
      );
    case "transfer":
    case "write-off": {
      if (!stock.data) return null;
      return operation.kind === "transfer" ? (
        <TransferQuantityDialog
          item={item}
          stock={stock.data}
          fromLocationId={operation.locationId}
          lookups={lookups}
          onClose={onClose}
        />
      ) : (
        <WriteOffQuantityDialog
          item={item}
          stock={stock.data}
          locationId={operation.locationId}
          lookups={lookups}
          onClose={onClose}
        />
      );
    }
  }
}

type SectionProps = {
  item: InventoryItem;
  lookups: InventoryLookups;
  onOperation?: (operation: ItemOperation) => void;
};

function ItemStockSection({ item, lookups, onOperation }: SectionProps) {
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
      {query.isSuccess && query.data.length === 0 ? (
        <EmptyState illustration="empty-inventory" title="Остатка нет" description="Позиции нет ни в одном месте хранения." />
      ) : null}
      {query.isSuccess && query.data.length > 0 ? (
        <ul className={styles.list} aria-label="Остатки по местам хранения">
          {query.data.map((row) => (
            <li key={row.storage_location_id}>
              <Card>
                <div className={styles.rowHeader}>
                  <span className={styles.rowTitle}>
                    {locationPath(row.storage_location_id, lookups.locations)}
                  </span>
                  <span className={styles.quantity}>{formatQuantity(row.quantity, unit)}</span>
                </div>
                {item.status === "active" && onOperation ? (
                  <StockRowActions row={row} onOperation={onOperation} />
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function StockRowActions({
  row,
  onOperation,
}: {
  row: InventoryStock;
  onOperation: (operation: ItemOperation) => void;
}) {
  return (
    <div className={styles.cardActions}>
      <Button
        variant="secondary"
        onClick={() => onOperation({ kind: "transfer", locationId: row.storage_location_id })}
      >
        Переместить
      </Button>
      <Button
        variant="secondary"
        onClick={() => onOperation({ kind: "write-off", locationId: row.storage_location_id })}
      >
        Списать
      </Button>
    </div>
  );
}

/** «Отменить списание» on a write-off of the shown history, unless a
 * reversal of it is already in that history; the backend decides. */
function writeOffReversalAction(
  movements: readonly InventoryMovement[],
  onReverse: (movement: InventoryMovement) => void,
) {
  const reversed = new Set(movements.map((movement) => movement.reverses_movement_id).filter(Boolean));
  return (movement: InventoryMovement) =>
    movement.movement_type === "write_off" ? (
      reversed.has(movement.id) ? (
        <span className={styles.muted}>Списание отменено</span>
      ) : (
        <Button variant="secondary" onClick={() => onReverse(movement)}>
          Отменить списание
        </Button>
      )
    ) : null;
}

function ItemMovementsSection({ item, lookups, onOperation }: SectionProps) {
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
        <EmptyState illustration="empty-inventory" title="Движений пока нет" />
      ) : null}
      {query.isSuccess && query.data.items.length > 0 ? (
        <>
          <MovementList
            movements={query.data.items}
            lookups={lookups}
            unit={unitName(item, lookups)}
            actions={
              onOperation
                ? writeOffReversalAction(query.data.items, (movement) => onOperation({ kind: "reverse", movement }))
                : undefined
            }
          />
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
