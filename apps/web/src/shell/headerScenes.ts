export const HEADER_SCENE_THEME = "basic" as const;

const HEADER_SCENE_ROOT = "/assets/ui/header-scenes-v1";
const HEADER_SCENE_SEQUENCE = /^([0-9]{2})$/;

export type HeaderSceneViewport = "desk" | "mob";

export type HeaderScene = {
  sequence: string;
  desktopSrc: string;
  mobileSrc: string;
};

const BASIC_SEQUENCES = [
  "01",
  "02",
  "03",
  "04",
  "05",
  "06",
  "07",
  "08",
  "09",
] as const;

function sceneSrc(viewport: HeaderSceneViewport, theme: string, sequence: string): string {
  return `${HEADER_SCENE_ROOT}/${viewport}-${theme}-${sequence}.png`;
}

function isSequence(value: string): boolean {
  return HEADER_SCENE_SEQUENCE.test(value);
}

export function getHeaderScenes(theme: string = HEADER_SCENE_THEME): readonly HeaderScene[] {
  const normalizedTheme = theme.trim().toLowerCase();
  const sequences = normalizedTheme === HEADER_SCENE_THEME ? BASIC_SEQUENCES : [];

  return sequences
    .filter(isSequence)
    .map((sequence) => ({
      sequence,
      desktopSrc: sceneSrc("desk", normalizedTheme, sequence),
      mobileSrc: sceneSrc("mob", normalizedTheme, sequence),
    }));
}

export function getHeaderSceneIndex(date: Date, sceneCount: number): number {
  if (sceneCount <= 0) return 0;

  const utcDate = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const epoch = Date.UTC(2026, 0, 1);
  const daysSinceEpoch = Math.floor((utcDate - epoch) / 86_400_000);

  return ((daysSinceEpoch % sceneCount) + sceneCount) % sceneCount;
}

export function getHeaderScene(date: Date = new Date(), theme: string = HEADER_SCENE_THEME): HeaderScene | null {
  const scenes = getHeaderScenes(theme);
  if (scenes.length === 0) return null;

  return scenes[getHeaderSceneIndex(date, scenes.length)] ?? null;
}
