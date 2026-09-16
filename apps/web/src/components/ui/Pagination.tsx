import { Button } from "./Button";
import styles from "./Pagination.module.css";

export type PaginationProps = {
  page: number;
  pages: number;
  total: number;
  onPageChange: (page: number) => void;
};

/** The standard ADR-0014 `{page, page_size, total, pages}` envelope,
 * rendered as simple previous/next controls — sufficient for the People
 * list (Issue #109) without a page-number widget this foundation
 * doesn't have yet. Hidden entirely when there is only one page. */
export function Pagination({ page, pages, total, onPageChange }: PaginationProps) {
  if (pages <= 1) return null;
  return (
    <nav className={styles.pagination} aria-label="Страницы результатов">
      <Button variant="secondary" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        Назад
      </Button>
      <span className={styles.summary}>
        Страница {page} из {pages} · всего {total}
      </span>
      <Button variant="secondary" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>
        Далее
      </Button>
    </nav>
  );
}
