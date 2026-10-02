import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useInventoryInstance,
  useInventoryInstanceMovements,
  type InventoryInstance,
  type InventoryMovement,
} from "../api/inventory";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/StatusBadge";
import { locationPath } from "../domain/inventoryFormat";
import { instanceStateIcon, instanceStateLabel } from "../domain/statusMapping";
import { unitName, useInventoryLookups } from "../hooks/useInventoryLookups";
import {
  InstanceEditDialog,
  InstanceRepairDialog,
  InstanceTransferDialog,
  InstanceWriteOffDialog,
  ReverseWriteOffDialog,
} from "./InventoryForms";
import { InventoryQueryError, MetaList, MovementList } from "./InventoryShared";
import styles from "./Inventory.module.css";

type InstanceOperation =
  | { kind: "edit" | "transfer" | "repair-start" | "repair-end" | "write-off" }
  | { kind: "reverse"; movement: InventoryMovement };

/** Operations offered for the instance's current state, as documented in
 * docs/04-domain/inventory.md §7.4 (state and location are never edited
 * directly). An issued instance is operated from its issue. The backend
 * re-checks every transition. */
function InstanceActions({
  instance,
  onOperation,
}: {
  instance: InventoryInstance;
  onOperation: (operation: InstanceOperation) => void;
}) {
  switch (instance.state) {
    case "available":
    case "in_repair":
      return (
        <div className={`${styles.actions} ${styles.pageActions}`} role="group" aria-label="Операции с экземпляром">
          <Button variant="primary" onClick={() => onOperation({ kind: "transfer" })}>
            Переместить
          </Button>
          {instance.state === "available" ? (
            <Button variant="secondary" onClick={() => onOperation({ kind: "repair-start" })}>
              В ремонт
            </Button>
          ) : (
            <Button variant="secondary" onClick={() => onOperation({ kind: "repair-end" })}>
              Завершить ремонт
            </Button>
          )}
          <Button variant="secondary" icon="action.edit" onClick={() => onOperation({ kind: "edit" })}>
            Изменить
          </Button>
          <Button variant="destructive" onClick={() => onOperation({ kind: "write-off" })}>
            Списать
          </Button>
        </div>
      );
    case "issued":
      return (
        <p className={styles.muted}>
          Экземпляр выдан. Возврат и утеря оформляются в карточке выдачи (вкладка «Выдачи»).
        </p>
      );
    case "written_off":
      return <p className={styles.muted}>Экземпляр списан. Отменить ошибочное списание можно в истории движений.</p>;
  }
}

/** One instance (Inventory ID): its identity, state and location, its
 * operations, then its chronological movement history. */
export function InventoryInstancePage() {
  const [operation, setOperation] = useState<InstanceOperation | null>(null);
  const { instanceId } = useParams<{ instanceId: string }>();
  const instanceQuery = useInventoryInstance(instanceId);
  const movementsQuery = useInventoryInstanceMovements(instanceId);
  const { lookups, isLoading, error, refetch } = useInventoryLookups();
  const item = instanceQuery.data ? lookups.items.get(instanceQuery.data.item_id) : undefined;
  const back = item
    ? { to: `/inventory/items/${item.id}`, label: item.name }
    : { to: "/inventory?tab=instances", label: "Склад" };

  if (instanceQuery.isLoading || isLoading) {
    return (
      <div>
        <PageHeader title="Экземпляр" back={back} />
        <Loading label="Загружаем экземпляр…" />
      </div>
    );
  }
  const failure = instanceQuery.error ?? error;
  if (failure) {
    return (
      <div>
        <PageHeader title="Экземпляр" back={back} />
        <InventoryQueryError
          error={failure}
          title="Не удалось загрузить экземпляр"
          pathParam="instance_id"
          onRetry={() => {
            void instanceQuery.refetch();
            refetch();
          }}
        />
      </div>
    );
  }
  const instance = instanceQuery.data;
  if (!instance) return null;
  const close = () => setOperation(null);
  const reversed = new Set((movementsQuery.data ?? []).map((movement) => movement.reverses_movement_id));
  // The latest write-off of a written-off instance is the one to reverse.
  const lastWriteOff =
    instance.state === "written_off"
      ? [...(movementsQuery.data ?? [])].reverse().find((movement) => movement.movement_type === "write_off")
      : undefined;

  return (
    <div className={styles.page}>
      <PageHeader
        title={instance.inventory_number}
        description={item?.name}
        back={back}
        titleExtra={
          <StatusBadge status={instanceStateIcon(instance.state)} label={instanceStateLabel(instance.state)} />
        }
      />
      <InstanceActions instance={instance} onOperation={setOperation} />
      <Card>
        <MetaList
          entries={[
            { label: "Место хранения", value: locationPath(instance.storage_location_id, lookups.locations) },
            { label: "Штрихкод производителя", value: instance.manufacturer_barcode ?? "—" },
            { label: "Серийный номер", value: instance.manufacturer_serial_number ?? "—" },
            { label: "Описание", value: instance.description ?? "—" },
          ]}
        />
      </Card>
      <section className={styles.section} aria-labelledby="inventory-instance-movements">
        <h2 id="inventory-instance-movements" className={styles.sectionTitle}>
          История движений
        </h2>
        {movementsQuery.isLoading ? <Loading label="Загружаем историю…" /> : null}
        {movementsQuery.isError ? (
          <InventoryQueryError
            error={movementsQuery.error}
            title="Не удалось загрузить историю"
            onRetry={() => void movementsQuery.refetch()}
          />
        ) : null}
        {movementsQuery.isSuccess && movementsQuery.data.length === 0 ? (
          <EmptyState illustration="empty-inventory" title="Движений пока нет" />
        ) : null}
        {movementsQuery.isSuccess && movementsQuery.data.length > 0 ? (
          <MovementList
            movements={movementsQuery.data}
            lookups={lookups}
            unit={unitName(item, lookups)}
            actions={(movement) =>
              movement.movement_type !== "write_off" ? null : reversed.has(movement.id) ? (
                <span className={styles.muted}>Списание отменено</span>
              ) : movement.id === lastWriteOff?.id ? (
                <Button variant="secondary" onClick={() => setOperation({ kind: "reverse", movement })}>
                  Отменить списание
                </Button>
              ) : null
            }
          />
        ) : null}
      </section>
      {operation?.kind === "edit" ? <InstanceEditDialog instance={instance} onClose={close} /> : null}
      {operation?.kind === "transfer" ? (
        <InstanceTransferDialog instance={instance} lookups={lookups} onClose={close} />
      ) : null}
      {operation?.kind === "repair-start" || operation?.kind === "repair-end" ? (
        <InstanceRepairDialog instance={instance} action={operation.kind} onClose={close} />
      ) : null}
      {operation?.kind === "write-off" ? <InstanceWriteOffDialog instance={instance} onClose={close} /> : null}
      {operation?.kind === "reverse" ? (
        <ReverseWriteOffDialog
          movement={operation.movement}
          accountingMode="instance"
          lookups={lookups}
          onClose={close}
        />
      ) : null}
    </div>
  );
}
