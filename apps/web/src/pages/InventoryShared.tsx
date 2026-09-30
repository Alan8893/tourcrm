import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import type { ApiError } from "../api/client";
import { useInventoryInstances, type InstanceState, type InventoryMovement } from "../api/inventory";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { Pagination } from "../components/ui/Pagination";
import { StatusBadge } from "../components/ui/StatusBadge";
import {
  formatCostMinor,
  formatMovementDate,
  instanceStateIcon,
  instanceStateLabel,
  locationPath,
  movementTypeLabel,
} from "../domain/inventoryFormat";
import type { InventoryLookups } from "../hooks/useInventoryLookups";
import styles from "./Inventory.module.css";

export type InventoryQueryErrorProps = {
  error: ApiError;
  title: string;
  onRetry: () => void;
};

/** Error state of an inventory request: `403` gets the standard
 * forbidden illustration (Administrator-only section); `401` means the
 * session ended, so the shell's `/auth/me` is re-checked and AppShell
 * hands the visitor to `/login`; anything else can be retried. */
export function InventoryQueryError({ error, title, onRetry }: InventoryQueryErrorProps) {
  const queryClient = useQueryClient();
  const unauthenticated = error.status === 401;

  useEffect(() => {
    if (unauthenticated) void queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
  }, [unauthenticated, queryClient]);

  if (error.status === 403) {
    return (
      <ErrorState
        illustration="403"
        title="Раздел недоступен"
        description="Склад доступен только администратору."
      />
    );
  }
  if (error.status === 404) {
    return <ErrorState illustration="404" title={title} description="Запись не найдена." />;
  }
  return (
    <ErrorState
      illustration="error"
      title={title}
      description={unauthenticated ? "Сессия завершилась. Войдите снова." : error.message}
      action={
        unauthenticated ? undefined : (
          <Button variant="secondary" onClick={onRetry}>
            Повторить
          </Button>
        )
      }
    />
  );
}

export type MetaEntry = { label: string; value: string };

/** Label/value pairs that reflow from several columns to one on a phone. */
export function MetaList({ entries }: { entries: MetaEntry[] }) {
  return (
    <dl className={styles.meta}>
      {entries.map((entry) => (
        <div key={entry.label} className={styles.metaEntry}>
          <dt className={styles.metaLabel}>{entry.label}</dt>
          <dd className={styles.metaValue}>{entry.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export type MovementListProps = {
  movements: readonly InventoryMovement[];
  lookups: InventoryLookups;
  unit: string;
};

/** The immutable movement journal, oldest first as the API returns it. */
export function MovementList({ movements, lookups, unit }: MovementListProps) {
  return (
    <ol className={styles.list} aria-label="История движений">
      {movements.map((movement) => {
        const entries: MetaEntry[] = [];
        if (movement.from_location_id) {
          entries.push({ label: "Откуда", value: locationPath(movement.from_location_id, lookups.locations) });
        }
        if (movement.to_location_id) {
          entries.push({ label: "Куда", value: locationPath(movement.to_location_id, lookups.locations) });
        }
        if (movement.quantity !== null) {
          entries.push({ label: "Количество", value: `${movement.quantity} ${unit}`.trim() });
        }
        if (movement.movement_type === "receipt" && movement.unit_cost_minor !== null) {
          entries.push({ label: "Стоимость за единицу", value: formatCostMinor(movement.unit_cost_minor) });
        }
        if (movement.comment) {
          entries.push({ label: "Комментарий", value: movement.comment });
        }
        return (
          <li key={movement.id}>
            <Card>
              <div className={styles.rowHeader}>
                <h3 className={styles.rowTitle}>{movementTypeLabel(movement.movement_type)}</h3>
                <time className={styles.rowAside} dateTime={movement.created_at}>
                  {formatMovementDate(movement.created_at)}
                </time>
              </div>
              {entries.length > 0 ? <MetaList entries={entries} /> : null}
            </Card>
          </li>
        );
      })}
    </ol>
  );
}

export type InstanceListProps = {
  lookups: InventoryLookups;
  filters: { itemId: string; state: InstanceState | ""; locationId: string; page: number };
  filtered: boolean;
  showItem: boolean;
  onPageChange: (page: number) => void;
};

/** Paginated instances matching `filters` — shared by the «Экземпляры»
 * tab and an instance-mode item's page. */
export function InstanceList({ lookups, filters, filtered, showItem, onPageChange }: InstanceListProps) {
  const query = useInventoryInstances(filters);

  if (query.isLoading) return <Loading label="Загружаем экземпляры…" />;
  if (query.isError) {
    return (
      <InventoryQueryError
        error={query.error}
        title="Не удалось загрузить экземпляры"
        onRetry={() => void query.refetch()}
      />
    );
  }
  if (!query.isSuccess) return null;
  if (query.data.items.length === 0) {
    return (
      <EmptyState
        illustration={filtered ? "no-results" : "empty-groups"}
        title={filtered ? "Ничего не найдено" : "Экземпляров пока нет"}
        description={
          filtered
            ? "Попробуйте изменить фильтры."
            : "Здесь появятся экземпляры позиций с поэкземплярным учётом."
        }
      />
    );
  }
  return (
    <>
      <ul className={styles.list} aria-label="Экземпляры">
        {query.data.items.map((instance) => {
          const entries = [
            ...(showItem
              ? [{ label: "Номенклатура", value: lookups.items.get(instance.item_id)?.name ?? "—" }]
              : []),
            { label: "Место хранения", value: locationPath(instance.storage_location_id, lookups.locations) },
            { label: "Штрихкод производителя", value: instance.manufacturer_barcode ?? "—" },
            { label: "Серийный номер", value: instance.manufacturer_serial_number ?? "—" },
            { label: "Описание", value: instance.description ?? "—" },
          ];
          return (
            <li key={instance.id}>
              <Card>
                <div className={styles.rowHeader}>
                  <h3 className={styles.rowTitle}>
                    <Link to={`/inventory/instances/${instance.id}`} className={styles.rowTitleLink}>
                      {instance.inventory_number}
                    </Link>
                  </h3>
                  <StatusBadge
                    status={instanceStateIcon(instance.state)}
                    label={instanceStateLabel(instance.state)}
                  />
                </div>
                <MetaList entries={entries} />
              </Card>
            </li>
          );
        })}
      </ul>
      <Pagination
        page={query.data.pagination.page}
        pages={query.data.pagination.pages}
        total={query.data.pagination.total}
        onPageChange={onPageChange}
      />
    </>
  );
}
