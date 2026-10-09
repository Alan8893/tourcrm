import { useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";

import { Avatar } from "../components/ui/Avatar";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { PageHeader } from "../components/ui/PageHeader";
import { useNotify } from "../components/ui/notificationContext";
import { displayName, useCurrentUser } from "../api/auth";
import { usePerson } from "../api/people";
import { currentUserPhotoUrl, useDeletePersonPhoto, useUploadPersonPhoto } from "../api/profilePhoto";
import { hasAdministratorRole } from "../shell/navigation";
import { PhotoCropDialog } from "./PhotoCropDialog";
import { SettingsPasswordForm } from "./SettingsPasswordForm";
import { SettingsProfileForm } from "./SettingsProfileForm";
import styles from "./SettingsPage.module.css";

/**
 * Settings/profile flow (reached from the ProfileMenu's «Настройки»).
 * TH-0119 adds the authenticated user's own profile photo management
 * (AVATAR-PHOTO-SPEC.md §9): current Avatar, «Изменить фотографию» → round
 * crop editor, and «Удалить фотографию» when a photo exists. Validation,
 * normalization and storage are entirely the backend's; after save/delete
 * the `auth/me` query is updated, so the ProfileMenu avatar changes
 * without a page reload.
 *
 * TH #311 adds the user's own Person data (`GET`/`PATCH
 * /persons/{person_id}`, `person.read(self)`/`person.update(self)`) and a
 * «Безопасность» section with the self-service password change. Whether
 * the Person data is available is decided by the backend: a role without
 * a `person.read` grant reaching its own Person gets the existence-hiding
 * 404, shown as a note rather than a form — no frontend permission model.
 */
export function SettingsPage() {
  const me = useCurrentUser();
  const notify = useNotify();
  const upload = useUploadPersonPhoto();
  const remove = useDeletePersonPhoto();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const person = usePerson(me.data?.user.person.id);

  if (me.isLoading) {
    return (
      <div>
        <PageHeader title="Настройки" />
        <Loading />
      </div>
    );
  }

  if (!me.data) {
    return (
      <div>
        <PageHeader title="Настройки" />
        <ErrorState
          illustration={me.error?.status === 403 ? "403" : "error"}
          title="Не удалось загрузить профиль"
          description={me.error?.message}
        />
      </div>
    );
  }

  const user = me.data.user;
  const personId = user.person.id;
  const name = displayName(user);
  const hasPhoto = Boolean(user.person.photo_file_id);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    // Reset so choosing the same file again still fires `change`.
    event.target.value = "";
    if (file) setSelectedFile(file);
  }

  function handleSave(photo: Blob) {
    upload.mutate(
      { personId, photo },
      {
        onSuccess: () => {
          setSelectedFile(null);
          notify("success", "Фотография обновлена");
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  function handleImageError() {
    setSelectedFile(null);
    notify("error", "Не удалось открыть изображение. Выберите файл JPEG, PNG или WebP.");
  }

  function handleDelete() {
    remove.mutate(
      { personId },
      {
        onSuccess: () => {
          setConfirmDelete(false);
          notify("success", "Фотография удалена");
        },
        onError: (error) => notify("error", error.message),
      },
    );
  }

  return (
    <div>
      <PageHeader title="Настройки" />
      <Card className={styles.profileCard}>
        <h2 className={styles.sectionTitle}>Профиль</h2>
        <div className={styles.profileRow}>
          <Avatar name={name} size={96} photoUrl={currentUserPhotoUrl(me.data)} />
          <div className={styles.profileInfo}>
            <div className={styles.name}>{name}</div>
            <div className={styles.actions}>
              <Button
                variant="secondary"
                icon="action.upload"
                onClick={() => fileInputRef.current?.click()}
                disabled={upload.isPending || remove.isPending}
              >
                Изменить фотографию
              </Button>
              {hasPhoto ? (
                <Button
                  variant="destructive"
                  icon="action.delete"
                  onClick={() => setConfirmDelete(true)}
                  disabled={upload.isPending || remove.isPending}
                >
                  Удалить фотографию
                </Button>
              ) : null}
            </div>
          </div>
        </div>
        <div className={styles.profileData}>
          {person.isLoading ? <Loading label="Загружаем данные профиля…" /> : null}
          {person.isError && (person.error.status === 404 || person.error.status === 403) ? (
            <p className={styles.note}>Изменение данных профиля в настройках вам недоступно.</p>
          ) : null}
          {person.isError && person.error.status !== 404 && person.error.status !== 403 ? (
            <div className={styles.loadError}>
              <p className={styles.error} role="alert">
                Не удалось загрузить данные профиля.
              </p>
              <Button variant="secondary" onClick={() => void person.refetch()}>
                Повторить
              </Button>
            </div>
          ) : null}
          {person.data ? (
            <SettingsProfileForm key={person.data.updated_at} person={person.data} />
          ) : null}
        </div>
        <input
          ref={fileInputRef}
          className={styles.fileInput}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          aria-label="Файл фотографии"
          data-testid="profile-photo-input"
          onChange={handleFileChange}
        />
      </Card>

      <Card className={styles.profileCard}>
        <h2 className={styles.sectionTitle}>Безопасность</h2>
        <h3 className={styles.subsectionTitle}>Изменить пароль</h3>
        <SettingsPasswordForm />
      </Card>

      {hasAdministratorRole(me.data.role_assignments) ? (
        <Card className={styles.profileCard}>
          <h2 className={styles.sectionTitle}>Администрирование</h2>
          <p className={styles.note}>
            Каналы Email и Telegram, глобальная политика уведомлений и тестовая отправка.
          </p>
          <p>
            <Link to="/settings/notifications">Уведомления</Link>
          </p>
        </Card>
      ) : null}

      <PhotoCropDialog
        file={selectedFile}
        saving={upload.isPending}
        onCancel={() => setSelectedFile(null)}
        onSave={handleSave}
        onImageError={handleImageError}
      />

      <ConfirmDialog
        open={confirmDelete}
        title="Удалить фотографию?"
        description="Вместо фотографии снова будут показаны инициалы."
        confirmLabel="Удалить фотографию"
        destructive
        pending={remove.isPending}
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
