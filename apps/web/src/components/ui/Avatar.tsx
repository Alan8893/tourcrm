import styles from "./Avatar.module.css";

export type AvatarProps = {
  /** Full display name of a signed-in person. Omit for a guest/unknown
   * state — no photo asset exists yet in the backend/domain model
   * (Person has no servable image), so the avatar is always a plain
   * initials/text mark rather than a generic placeholder icon or SVG. */
  name?: string;
  size?: number;
};

function initialsFrom(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0] + parts[1]![0]).toUpperCase();
}

export function Avatar({ name, size = 36 }: AvatarProps) {
  const initials = name ? initialsFrom(name) : "";
  return (
    <span
      className={`${styles.avatar} ${name ? "" : styles.guest}`}
      style={{ width: size, height: size, fontSize: size * 0.4 }}
      aria-hidden="true"
    >
      {initials || "?"}
    </span>
  );
}
