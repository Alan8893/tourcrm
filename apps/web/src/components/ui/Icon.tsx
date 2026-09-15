import { BRAND_LOGO_PATH, ICON_SOURCES } from "../../assets/icons";
import type { IconId, IconSize, IllustrationId, IllustrationSize } from "../../assets/icons";
import { illustrationPath } from "../../assets/icons";

export type IconProps = {
  id: IconId;
  size?: IconSize;
  /** Provide only when the icon is the sole content conveying meaning
   * (e.g. no adjacent visible label). Icons inside a labelled control or
   * next to visible text should stay decorative (omit `alt`) so screen
   * readers do not announce the meaning twice. */
  alt?: string;
  className?: string;
};

export function Icon({ id, size = 24, alt, className }: IconProps) {
  const src = ICON_SOURCES[id](size);
  return (
    <img
      src={src}
      width={size}
      height={size}
      alt={alt ?? ""}
      className={className}
      draggable={false}
    />
  );
}

export type IllustrationProps = {
  id: IllustrationId;
  size?: IllustrationSize;
  alt?: string;
  className?: string;
};

export function Illustration({ id, size = 128, alt, className }: IllustrationProps) {
  return (
    <img
      src={illustrationPath(id, size)}
      width={size}
      height={size}
      alt={alt ?? ""}
      className={className}
      draggable={false}
    />
  );
}

export type BrandLogoProps = {
  height?: number;
  className?: string;
};

export function BrandLogo({ height = 36, className }: BrandLogoProps) {
  return (
    <img
      src={BRAND_LOGO_PATH}
      alt="TourCRM «Вектор»"
      height={height}
      className={className}
      draggable={false}
    />
  );
}
