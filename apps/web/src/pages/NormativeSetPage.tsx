import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  useCreateNormativeVersion,
  useNormativeSet,
  useNormativeVersions,
  useSetNormativeVersionStatus,
  useUpdateNormativeVersion,
  type NormativeVersion,
  type NormativeVersionFields,
} from "../api/achievements";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { StatusBadge } from "../components/ui/StatusBadge";
import { useNotify } from "../components/ui/notificationContext";
import {
  LIFECYCLE_LABELS,
  achievementErrorMessage,
  formatDate,
  lifecycleIcon,
} from "../domain/achievementsFormat";
import { Meta } from "./AchievementsPage";
import { FormDialog } from "./InventoryForms";
import styles from "./Achievements.module.css";

/** Normative Requirement Set detail (Issue #220; achievements-and-
 * norms.md §4–§6): versions with their source metadata, activation /
 * deactivation and review. A version used by an Award is immutable — the
 * backend refuses the edit, and the UI only offers creating a new
 * version. Administrator-only route. */
export function NormativeSetPage() {
  const { setId } = useParams();
  const set = useNormativeSet(setId);
  const versions = useNormativeVersions(setId);
  const setStatus = useSetNormativeVersionStatus();
  const notify = useNotify();
  const [editor, setEditor] = useState<{ version: NormativeVersion | null } | null>(null);

  if (set.isLoading) return <Loading label="Загружаем нормативы…" />;
  if (set.isError || !set.data) return <ErrorState illustration="404" title="Набор нормативов не найден" />;

  return (
    <div className={styles.page}>
      <PageHeader
        title={set.data.name}
        description={set.data.description ?? undefined}
        back={{ to: "/achievements", label: "Достижения" }}
        actions={
          <Button variant="primary" icon="action.add" onClick={() => setEditor({ version: null })}>
            Новая версия нормативов
          </Button>
        }
      />
      <p className={styles.muted}>
        Версия создаётся неактивной. Версия, использованная в выданных достижениях, не изменяется.
      </p>
      {versions.isLoading ? <Loading label="Загружаем версии…" /> : null}
      {versions.isError ? <ErrorState illustration="error" title="Не удалось загрузить версии" /> : null}
      {versions.data && versions.data.items.length === 0 ? (
        <EmptyState illustration="no-results" title="Версий нормативов пока нет" />
      ) : null}
      {versions.data && versions.data.items.length > 0 ? (
        <ul className={styles.list}>
          {versions.data.items.map((version) => (
            <li key={version.id}>
              <Card data-testid="normative-version-card">
                <div className={styles.rowHeader}>
                  <h3 className={styles.rowTitle}>
                    Версия {version.version_number}: {version.document_title}
                  </h3>
                  <StatusBadge status={lifecycleIcon(version.status)} label={LIFECYCLE_LABELS[version.status]} />
                </div>
                <dl className={styles.meta}>
                  <Meta label="Организация" value={version.source_organization} />
                  <Meta label="Редакция документа" value={version.document_version} />
                  <Meta label="Источник" value={version.source_url} />
                  <Meta label="Дата публикации" value={formatDate(version.publication_date)} />
                  <Meta label="Действует с" value={formatDate(version.effective_from)} />
                  <Meta label="Действует по" value={formatDate(version.effective_to)} />
                  <Meta label="Использована в выдачах" value={version.is_used ? "Да" : "Нет"} />
                </dl>
                <div className={styles.cardActions}>
                  <Button
                    variant="secondary"
                    disabled={setStatus.isPending}
                    onClick={() =>
                      setStatus.mutate(
                        { versionId: version.id, active: version.status !== "active" },
                        { onError: (error) => notify("error", achievementErrorMessage(error)) },
                      )
                    }
                  >
                    {version.status === "active" ? "Деактивировать" : "Активировать"}
                  </Button>
                  {!version.is_used ? (
                    <Button variant="secondary" onClick={() => setEditor({ version })}>
                      Изменить
                    </Button>
                  ) : null}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      ) : null}
      {editor && setId ? (
        <NormativeVersionDialog setId={setId} version={editor.version} onClose={() => setEditor(null)} />
      ) : null}
    </div>
  );
}

function NormativeVersionDialog({
  setId,
  version,
  onClose,
}: {
  setId: string;
  version: NormativeVersion | null;
  onClose: () => void;
}) {
  const [fields, setFields] = useState<NormativeVersionFields>({
    source_organization: version?.source_organization ?? "",
    document_title: version?.document_title ?? "",
    source_url: version?.source_url ?? "",
    document_version: version?.document_version ?? "",
    publication_date: version?.publication_date ?? null,
    effective_from: version?.effective_from ?? "",
    effective_to: version?.effective_to ?? null,
  });
  const create = useCreateNormativeVersion();
  const update = useUpdateNormativeVersion();
  const mutation = version ? update : create;
  const notify = useNotify();

  function text(key: keyof NormativeVersionFields) {
    return (event: { target: { value: string } }) => setFields({ ...fields, [key]: event.target.value });
  }
  function optionalDate(key: "publication_date" | "effective_to") {
    return (event: { target: { value: string } }) =>
      setFields({ ...fields, [key]: event.target.value || null });
  }

  function submit() {
    const onSuccess = () => {
      notify("success", version ? "Версия нормативов сохранена" : "Версия нормативов создана");
      onClose();
    };
    if (version) update.mutate({ versionId: version.id, fields }, { onSuccess });
    else create.mutate({ setId, fields }, { onSuccess });
  }

  return (
    <FormDialog
      title={version ? `Версия нормативов ${version.version_number}` : "Новая версия нормативов"}
      submitLabel={version ? "Сохранить" : "Создать"}
      pending={mutation.isPending}
      error={mutation.isError ? achievementErrorMessage(mutation.error) : null}
      onClose={onClose}
      onSubmit={submit}
    >
      <Input label="Организация-источник" value={fields.source_organization} onChange={text("source_organization")} />
      <Input label="Название документа" value={fields.document_title} onChange={text("document_title")} />
      <Input label="Ссылка на источник" value={fields.source_url} onChange={text("source_url")} />
      <Input label="Редакция документа" value={fields.document_version} onChange={text("document_version")} />
      <Input
        label="Дата публикации"
        type="date"
        value={fields.publication_date ?? ""}
        onChange={optionalDate("publication_date")}
      />
      <Input label="Действует с" type="date" value={fields.effective_from} onChange={text("effective_from")} />
      <Input
        label="Действует по"
        type="date"
        value={fields.effective_to ?? ""}
        onChange={optionalDate("effective_to")}
      />
    </FormDialog>
  );
}
