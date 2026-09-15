/**
 * Registry of approved production icon assets (TH-0088), integrated from
 * the approved packages under docs/06-ui/assets/packages/ into
 * public/assets/ui/ per docs/06-ui/assets/INTEGRATION-HANDOFF.md.
 *
 * Every id here corresponds to a stable semantic id in
 * docs/06-ui/assets/asset-manifest.json (`icon.navigation.*`,
 * `icon.action.*`, `icon.status.*`, `icon.domain.*`, prefix dropped for
 * brevity). No generic icon-library substitute and no SVG is used —
 * every source file is a bespoke TourCRM WebP export at one of the
 * approved sizes (16/20/24/32/48/64).
 */

export type IconSize = 16 | 20 | 24 | 32 | 48 | 64;

export type NavigationIconId =
  | "nav.home"
  | "nav.people"
  | "nav.groups"
  | "nav.events"
  | "nav.achievements"
  | "nav.reports"
  | "nav.settings";

export type ActionIconId =
  | "action.add"
  | "action.edit"
  | "action.delete"
  | "action.archive"
  | "action.restore"
  | "action.search"
  | "action.filter"
  | "action.sort"
  | "action.save"
  | "action.cancel"
  | "action.confirm"
  | "action.close"
  | "action.back"
  | "action.forward"
  | "action.more"
  | "action.download"
  | "action.upload";

export type StatusIconId =
  | "status.planned"
  | "status.ongoing"
  | "status.completed"
  | "status.ended"
  | "status.archived"
  | "status.success"
  | "status.warning"
  | "status.error"
  | "status.info";

export type DomainIconId =
  | "domain.membership"
  | "domain.group-members"
  | "domain.group-instructor"
  | "domain.primary-instructor";

export type IconId = NavigationIconId | ActionIconId | StatusIconId | DomainIconId;

function navPath(slug: string) {
  return (size: IconSize) => `/assets/ui/icons/navigation/${slug}/${slug}_${size}px.webp`;
}

function actionPath(slug: string) {
  return (size: IconSize) => `/assets/ui/icons/actions/${slug}/${slug}_${size}px.webp`;
}

function statusPath(slug: string) {
  return (size: IconSize) => `/assets/ui/icons/status/${slug}/${slug}_${size}px.webp`;
}

// Domain package files keep their original manifest-documented naming
// (DOM-XXX_slug_SIZE.webp, no per-icon subfolder, no "px" suffix) rather
// than being renamed to match the other families' convention during
// integration — see asset-manifest.json's own `icon.domain.*` paths.
function domainPath(fileSlug: string) {
  return (size: IconSize) => `/assets/ui/icons/domain/${fileSlug}_${size}.webp`;
}

export const ICON_SOURCES: Record<IconId, (size: IconSize) => string> = {
  "nav.home": navPath("home"),
  "nav.people": navPath("people"),
  "nav.groups": navPath("groups"),
  "nav.events": navPath("events"),
  "nav.achievements": navPath("achievements"),
  "nav.reports": navPath("reports"),
  "nav.settings": navPath("settings"),

  "action.add": actionPath("add"),
  "action.edit": actionPath("edit"),
  "action.delete": actionPath("delete"),
  "action.archive": actionPath("archive"),
  "action.restore": actionPath("restore"),
  "action.search": actionPath("search"),
  "action.filter": actionPath("filter"),
  "action.sort": actionPath("sort"),
  "action.save": actionPath("save"),
  "action.cancel": actionPath("cancel"),
  "action.confirm": actionPath("confirm"),
  "action.close": actionPath("close"),
  "action.back": actionPath("back"),
  "action.forward": actionPath("forward"),
  "action.more": actionPath("more"),
  "action.download": actionPath("download"),
  "action.upload": actionPath("upload"),

  "status.planned": statusPath("planned"),
  "status.ongoing": statusPath("ongoing"),
  "status.completed": statusPath("completed"),
  "status.ended": statusPath("ended"),
  "status.archived": statusPath("archived"),
  "status.success": statusPath("success"),
  "status.warning": statusPath("warning"),
  "status.error": statusPath("error"),
  "status.info": statusPath("info"),

  "domain.membership": domainPath("DOM-006_membership"),
  "domain.group-members": domainPath("DOM-010_group-members"),
  "domain.group-instructor": domainPath("DOM-011_group-instructor"),
  "domain.primary-instructor": domainPath("DOM-012_primary-instructor"),
};

/** Approved illustration families integrated for this stage: Empty states
 * (`empty-groups`, `no-results`) and System (`error`, `403`, `404`).
 * Onboarding illustrations are not wired into this Issue's three
 * reference screens and are left for a later feature slice. */
export type IllustrationId = "empty-groups" | "no-results" | "error" | "403" | "404";
export type IllustrationSize = 32 | 64 | 128 | 256;

export function illustrationPath(id: IllustrationId, size: IllustrationSize): string {
  return `/assets/ui/illustrations/${id}_${size}px.webp`;
}

export const BRAND_LOGO_PATH = "/assets/ui/brand/logo.png";
