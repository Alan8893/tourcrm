export const HEADER_SCENE_THEME = "basic" as const;

const HEADER_SCENE_ROOT = "/assets/ui/header-scenes-v1";
const UTC_DAY_MS = 86_400_000;
const ROTATION_EPOCH_UTC = Date.UTC(2026, 0, 1);
const BASIC_SEQUENCES = ["01", "02", "03", "04", "05", "06", "07", "08", "09"] as const;

export type HeaderScene = { sequence: string; desktopSrc: string; mobileSrc: string };

function sceneSrc(viewport: "desk" | "mob", theme: string, sequence: string): string {
  return `${HEADER_SCENE_ROOT}/${viewport}-${theme}-${sequence}.png`;
}

export function getHeaderScenes(theme: string = HEADER_SCENE_THEME): readonly HeaderScene[] {
  const normalizedTheme = theme.trim().toLowerCase();
  const sequences = normalizedTheme === HEADER_SCENE_THEME ? BASIC_SEQUENCES : [];
  return sequences.map((sequence) => ({
    sequence,
    desktopSrc: sceneSrc("desk", normalizedTheme, sequence),
    mobileSrc: sceneSrc("mob", normalizedTheme, sequence),
  }));
}

export function getHeaderSceneIndex(date: Date, sceneCount: number): number {
  if (sceneCount <= 0) return 0;
  const utcDate = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const daysSinceEpoch = Math.floor((utcDate - ROTATION_EPOCH_UTC) / UTC_DAY_MS);
  return ((daysSinceEpoch % sceneCount) + sceneCount) % sceneCount;
}

export function getHeaderScene(date: Date = new Date(), theme: string = HEADER_SCENE_THEME): HeaderScene | null {
  const scenes = getHeaderScenes(theme);
  if (scenes.length === 0) return null;
  return scenes[getHeaderSceneIndex(date, scenes.length)] ?? null;
}
