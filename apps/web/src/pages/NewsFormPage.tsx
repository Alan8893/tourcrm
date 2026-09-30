import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Loading } from "../components/ui/Loading";
import { ErrorState } from "../components/ui/ErrorState";
import { SearchInput } from "../components/ui/SearchInput";
import { useNotify } from "../components/ui/notificationContext";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useGroups } from "../api/groups";
import { useEventSearch } from "../api/events";
import {
  newsImageUrl,
  useCreateNews,
  useDeleteNewsImage,
  useNews,
  usePublishNews,
  useUpdateNews,
  useUploadNewsImage,
  type News,
  type NewsAudienceType,
  type NewsFields,
} from "../api/news";
import inputStyles from "../components/ui/Input.module.css";
import styles from "./News.module.css";

type FormState = {
  title: string;
  body: string;
  audienceType: NewsAudienceType;
  groupIds: string[];
  eventDate: string;
  location: string;
  eventId: string;
  publishNow: boolean;
};

const EMPTY_FORM: FormState = {
  title: "",
  body: "",
  audienceType: "club",
  groupIds: [],
  eventDate: "",
  location: "",
  eventId: "",
  publishNow: false,
};

function formFromNews(news: News): FormState {
  return {
    title: news.title,
    body: news.body,
    audienceType: news.audience_type,
    groupIds: news.group_ids ?? [],
    eventDate: news.event_date ?? "",
    location: news.location ?? "",
    eventId: news.linked_event?.id ?? "",
    publishNow: false,
  };
}

function toFields(form: FormState): NewsFields {
  return {
    title: form.title.trim(),
    body: form.body,
    audience_type: form.audienceType,
    group_ids: form.audienceType === "groups" ? form.groupIds : [],
    event_date: form.eventDate || null,
    location: form.location.trim() || null,
    event_id: form.eventId || null,
  };
}

/** Create/Edit News page (TH-0120 / Issue #227) — Administrator only
 * (route-guarded; the backend is authoritative). `/news/manage/new`
 * creates, `/news/manage/:newsId/edit` edits. The audience is only
 * *configured* here — who actually sees the News is resolved by the
 * backend. */
export function NewsFormPage() {
  const { newsId } = useParams();
  const newsQuery = useNews(newsId);

  if (newsId && newsQuery.isLoading) {
    return <Loading label="Загружаем новость…" />;
  }
  if (newsId && newsQuery.isError) {
    return (
      <ErrorState
        illustration={newsQuery.error.status === 404 ? "404" : "error"}
        title="Не удалось загрузить новость"
        description={newsQuery.error.message}
      />
    );
  }
  if (newsId && newsQuery.data?.status === "archived") {
    return (
      <ErrorState
        illustration="error"
        title="Архивную новость нельзя изменить"
        description="Новость находится в архиве."
      />
    );
  }

  return <NewsForm key={newsQuery.data?.id ?? "new"} news={newsId ? newsQuery.data : undefined} />;
}

function NewsForm({ news }: { news: News | undefined }) {
  const isEdit = Boolean(news);
  const [form, setForm] = useState<FormState>(() => (news ? formFromNews(news) : EMPTY_FORM));
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [removeImage, setRemoveImage] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const notify = useNotify();

  const createNews = useCreateNews();
  const updateNews = useUpdateNews();
  const publishNews = usePublishNews();
  const uploadImage = useUploadNewsImage();
  const deleteImage = useDeleteNewsImage();

  const previewUrl = useMemo(() => (imageFile ? URL.createObjectURL(imageFile) : null), [imageFile]);
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);
  const currentImageUrl = news && !removeImage ? newsImageUrl(news) : null;

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  const titleMissing = !form.title.trim();
  const bodyMissing = !form.body.trim();
  const groupsMissing = form.audienceType === "groups" && form.groupIds.length === 0;
  const invalid = titleMissing || bodyMissing || groupsMissing;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (invalid || submitting) return;
    setSubmitError(null);
    setSubmitting(true);
    let createdId: string | null = null;
    try {
      const fields = toFields(form);
      let saved: News;
      if (news) {
        saved = await updateNews.mutateAsync({ newsId: news.id, fields });
      } else {
        saved = await createNews.mutateAsync({
          ...fields,
          status: form.publishNow ? "published" : "draft",
        });
        createdId = saved.id;
      }
      if (imageFile) {
        saved = await uploadImage.mutateAsync({ newsId: saved.id, image: imageFile });
      } else if (news && removeImage && news.image_file_id) {
        saved = await deleteImage.mutateAsync(saved.id);
      }
      if (news && form.publishNow && saved.status === "draft") {
        saved = await publishNews.mutateAsync(saved.id);
      }
      notify(
        "success",
        saved.status === "published" ? "Новость сохранена и опубликована" : "Новость сохранена",
      );
      navigate("/news/manage");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Не удалось сохранить новость";
      if (createdId) {
        // The News itself exists already (e.g. only the image upload
        // failed): continue on its edit page so a retry never creates a
        // duplicate.
        notify("error", `Новость создана, но не всё сохранено: ${message}`);
        navigate(`/news/manage/${createdId}/edit`);
      } else {
        setSubmitError(message);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <PageHeader
        title={isEdit ? "Редактирование новости" : "Новая новость"}
        back={{ to: "/news/manage", label: "Управление новостями" }}
      />
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <Input
          label="Заголовок"
          value={form.title}
          maxLength={255}
          required
          onChange={(event) => update("title", event.target.value)}
        />
        <TextArea label="Текст" value={form.body} onChange={(value) => update("body", value)} />

        <ImageField
          currentUrl={currentImageUrl}
          previewUrl={previewUrl}
          onSelect={(file) => {
            setImageFile(file);
            if (file) setRemoveImage(false);
          }}
          onRemove={() => {
            setImageFile(null);
            setRemoveImage(true);
          }}
        />

        <div className={styles.row}>
          <Input
            label="Дата события"
            type="date"
            value={form.eventDate}
            onChange={(event) => update("eventDate", event.target.value)}
          />
          <Input
            label="Место"
            value={form.location}
            maxLength={255}
            onChange={(event) => update("location", event.target.value)}
          />
        </div>

        <EventPicker
          value={form.eventId}
          current={news?.linked_event ?? null}
          onChange={(value) => update("eventId", value)}
        />

        <AudienceField
          audienceType={form.audienceType}
          groupIds={form.groupIds}
          onAudienceChange={(value) => update("audienceType", value)}
          onGroupsChange={(value) => update("groupIds", value)}
          showError={groupsMissing}
        />

        {!news || news.status === "draft" ? (
          <fieldset className={styles.field}>
            <legend className={styles.label}>Статус публикации</legend>
            <div className={styles.choices}>
              <label className={styles.choice}>
                <input
                  type="radio"
                  name="news-status"
                  checked={!form.publishNow}
                  onChange={() => update("publishNow", false)}
                />
                Черновик
              </label>
              <label className={styles.choice}>
                <input
                  type="radio"
                  name="news-status"
                  checked={form.publishNow}
                  onChange={() => update("publishNow", true)}
                />
                Опубликовать
              </label>
            </div>
          </fieldset>
        ) : (
          <p className={styles.hint}>Новость опубликована. Изменения сразу станут видны читателям.</p>
        )}

        {submitError ? (
          <p className={styles.errorText} role="alert">
            {submitError}
          </p>
        ) : null}

        <div className={styles.formActions}>
          <Button type="submit" variant="primary" icon="action.save" disabled={invalid || submitting}>
            {isEdit ? "Сохранить" : "Создать"}
          </Button>
          <Button type="button" variant="secondary" onClick={() => navigate("/news/manage")}>
            Отмена
          </Button>
        </div>
      </form>
    </div>
  );
}

function TextArea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className={styles.textarea}
        value={value}
        required
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function ImageField({
  currentUrl,
  previewUrl,
  onSelect,
  onRemove,
}: {
  currentUrl: string | null;
  previewUrl: string | null;
  onSelect: (file: File | null) => void;
  onRemove: () => void;
}) {
  const id = useId();
  const shown = previewUrl ?? currentUrl;
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        Изображение (необязательно)
      </label>
      {shown ? <img className={styles.imagePreview} src={shown} alt="Предпросмотр изображения" /> : null}
      <input
        id={id}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        onChange={(event) => onSelect(event.target.files?.[0] ?? null)}
      />
      <span className={styles.hint}>JPEG, PNG или WebP, до 10 МБ.</span>
      {shown ? (
        <div>
          <Button type="button" variant="secondary" icon="action.delete" onClick={onRemove}>
            Убрать изображение
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function EventPicker({
  value,
  current,
  onChange,
}: {
  value: string;
  current: News["linked_event"];
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search, 300);
  const eventsQuery = useEventSearch(debounced);
  const options = useMemo(() => {
    const items = (eventsQuery.data?.items ?? []).map((event) => ({
      id: event.id,
      title: event.title,
      start_at: event.start_at,
    }));
    if (current && !items.some((item) => item.id === current.id)) {
      items.unshift({ id: current.id, title: current.title, start_at: current.start_at });
    }
    return items;
  }, [eventsQuery.data, current]);

  return (
    <div className={styles.field}>
      <SearchInput label="Найти событие" value={search} onChange={setSearch} placeholder="Название события" />
      <label className={styles.label} htmlFor={id}>
        Связанное событие (необязательно)
      </label>
      <select
        id={id}
        className={inputStyles.select}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Без события</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.title} — {new Date(option.start_at).toLocaleDateString("ru-RU")}
          </option>
        ))}
      </select>
      {eventsQuery.isError ? (
        <p className={styles.errorText}>Не удалось загрузить события: {eventsQuery.error.message}</p>
      ) : null}
    </div>
  );
}

function AudienceField({
  audienceType,
  groupIds,
  onAudienceChange,
  onGroupsChange,
  showError,
}: {
  audienceType: NewsAudienceType;
  groupIds: string[];
  onAudienceChange: (value: NewsAudienceType) => void;
  onGroupsChange: (value: string[]) => void;
  showError: boolean;
}) {
  const groupsQuery = useGroups();
  // Active groups, plus any already-selected group that has since been
  // archived (so an edit never silently drops it from view).
  const groups = (groupsQuery.data?.items ?? []).filter(
    (group) => group.status === "active" || groupIds.includes(group.id),
  );

  function toggle(groupId: string) {
    onGroupsChange(
      groupIds.includes(groupId) ? groupIds.filter((id) => id !== groupId) : [...groupIds, groupId],
    );
  }

  return (
    <fieldset className={styles.field}>
      <legend className={styles.label}>Аудитория</legend>
      <div className={styles.choices}>
        <label className={styles.choice}>
          <input
            type="radio"
            name="news-audience"
            checked={audienceType === "club"}
            onChange={() => onAudienceChange("club")}
          />
          Весь клуб
        </label>
        <label className={styles.choice}>
          <input
            type="radio"
            name="news-audience"
            checked={audienceType === "groups"}
            onChange={() => onAudienceChange("groups")}
          />
          Выбранные группы
        </label>
      </div>
      {audienceType === "groups" ? (
        <>
          {groupsQuery.isLoading ? <Loading label="Загружаем группы…" /> : null}
          {groupsQuery.isError ? (
            <p className={styles.errorText}>Не удалось загрузить группы: {groupsQuery.error.message}</p>
          ) : null}
          {groupsQuery.isSuccess ? (
            <ul className={styles.groupList} aria-label="Группы">
              {groups.map((group) => (
                <li key={group.id}>
                  <label className={styles.choice}>
                    <input
                      type="checkbox"
                      checked={groupIds.includes(group.id)}
                      onChange={() => toggle(group.id)}
                    />
                    {group.name}
                  </label>
                </li>
              ))}
              {groups.length === 0 ? <li className={styles.hint}>Нет активных групп</li> : null}
            </ul>
          ) : null}
          {showError ? <p className={styles.errorText}>Выберите хотя бы одну группу.</p> : null}
        </>
      ) : null}
    </fieldset>
  );
}
