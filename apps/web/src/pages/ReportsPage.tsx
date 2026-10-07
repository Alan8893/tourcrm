import { PageHeader } from "../components/ui/PageHeader";
import { LinkCard } from "../components/ui/Card";
import styles from "./ImportExport.module.css";

/**
 * «Отчёты» (Administrator-only section, information-architecture.md §3.1).
 * Participant Export is reached from here as «Отчёты → Экспорт»
 * (docs/04-ux/import-export-ui.md §3.2), and the «Участники мероприятий»
 * report over the same dataset as «Отчёты → Участники мероприятий» (§3.3,
 * Issue #299) — sub-pages of this section, not new global navigation items.
 */
export function ReportsPage() {
  return (
    <div>
      <PageHeader title="Отчёты" description="Выгрузки и отчёты по данным клуба." />
      <div className={styles.entryGrid}>
        <LinkCard to="/reports/event-participants">
          <h2 className={styles.entryTitle}>Участники мероприятий</h2>
          <p className={styles.muted}>
            Кто зарегистрирован на мероприятие: предпросмотр по клубу, группе или событию с фильтром
            статуса регистрации и выгрузкой в XLSX, PDF или для печати.
          </p>
        </LinkCard>
        <LinkCard to="/reports/export">
          <h2 className={styles.entryTitle}>Экспорт участников</h2>
          <p className={styles.muted}>
            Списки участников клуба, группы или события в XLSX, PDF или для печати — с выбором
            полей и фильтров.
          </p>
        </LinkCard>
      </div>
    </div>
  );
}
