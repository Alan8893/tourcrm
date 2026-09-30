import { useParams } from "react-router-dom";

import { useInventoryInstance, useInventoryInstanceMovements } from "../api/inventory";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/StatusBadge";
import { instanceStateIcon, instanceStateLabel, locationPath } from "../domain/inventoryFormat";
import { unitName, useInventoryLookups } from "../hooks/useInventoryLookups";
import { InventoryQueryError, MetaList, MovementList } from "./InventoryShared";
import styles from "./Inventory.module.css";

/** One instance (Inventory ID): its identity, state and location, then
 * its chronological movement history. */
export function InventoryInstancePage() {
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

  return (
    <div>
      <PageHeader
        title={instance.inventory_number}
        description={item?.name}
        back={back}
        titleExtra={
          <StatusBadge status={instanceStateIcon(instance.state)} label={instanceStateLabel(instance.state)} />
        }
      />
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
          <EmptyState illustration="empty-groups" title="Движений пока нет" />
        ) : null}
        {movementsQuery.isSuccess && movementsQuery.data.length > 0 ? (
          <MovementList movements={movementsQuery.data} lookups={lookups} unit={unitName(item, lookups)} />
        ) : null}
      </section>
    </div>
  );
}
