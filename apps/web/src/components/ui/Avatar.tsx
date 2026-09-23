import { useState } from "react";

import styles from "./Avatar.module.css";

export type AvatarProps = {
  /** Full display name of a signed-in person. Omit for a guest/unknown
   * state — the avatar is then a plain initials/text mark rather than a
   * generic placeholder icon or SVG. */
  name?: string;
  size?: number;
  /** TH-0119: versioned URL of the person's current profile photo
   * (`personPhotoUrl`). Omit when `photo_file_id` is null — the existing
   * initials mark is then rendered unchanged. If the photo cannot be
   * loaded, the initials mark is shown instead. */
  photoUrl?: string;
};

function initialsFrom(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0] + parts[1]![0]).toUpperCase();
}

export function Avatar({ name, size = 36, photoUrl }: AvatarProps) {
  const initials = name ? initialsFrom(name) : "";
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showPhoto = Boolean(photoUrl) && failedUrl !== photoUrl;
  return (
    <span
      className={`${styles.avatar} ${name ? "" : styles.guest} ${showPhoto ? styles.withPhoto : ""}`}
      style={{ width: size, height: size, fontSize: size * 0.4 }}
      aria-hidden="true"
    >
      {showPhoto ? (
        <img
          className={styles.photo}
          src={photoUrl}
          alt=""
          draggable={false}
          onError={() => setFailedUrl(photoUrl ?? null)}
        />
      ) : (
        initials || "?"
      )}
    </span>
  );
}
