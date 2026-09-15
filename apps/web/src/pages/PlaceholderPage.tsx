import { PageHeader } from "../components/ui/PageHeader";
import styles from "./PlaceholderPage.module.css";

export type PlaceholderPageProps = {
  title: string;
};

/**
 * TH-0088 validates the App Shell/UI Foundation against exactly three
 * reference screens (Home, Groups list, Group detail) — the other four
 * approved navigation destinations are real, reachable routes (so the
 * seven-item navigation is fully wired) but their own screens are a
 * later feature slice. No illustration asset is used here: none of the
 * approved System/Empty illustrations documents "not implemented yet" as
 * their meaning, and repurposing one would contradict ASSET-STATUS.md's
 * own "illustrations correspond to their actual state" rule.
 */
export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <div>
      <PageHeader title={title} />
      <div className={styles.wrapper}>
        <p className={styles.title}>Раздел появится в одном из следующих этапов.</p>
        <p>Навигация и структура приложения уже готовы для него.</p>
      </div>
    </div>
  );
}
