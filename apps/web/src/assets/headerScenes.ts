export type HeaderTheme = "basic";

export type HeaderScenePair = {
  sequence: number;
  desktop: string;
  mobile: string;
};

export const HEADER_SCENE_RUNTIME_BASE = "/assets/ui/header-scenes-v1";
export const HEADER_SCENE_EPOCH_MS = Date.UTC(2026, 0, 1);
export const DEFAULT_HEADER_THEME: HeaderTheme = "basic";

export const BASIC_HEADER_SCENES: readonly HeaderScenePair[] = Array.from(
  { length: 9 },
  (_, index) => {
    const sequence = index + 1;
    const suffix = String(sequence).padStart(2, "0");
    return {
      sequence,
      desktop: `${HEADER_SCENE_RUNTIME_BASE}/desk-basic-${suffix}.png`,
      mobile: `${HEADER_SCENE_RUNTIME_BASE}/mob-basic-${suffix}.png`,
    };
  },
);

export type HeaderSceneCatalog = Readonly<Record<string, readonly HeaderScenePair[]>>;

export function completeHeaderScenePairs(
  desktopSequences: readonly number[],
  mobileSequences: readonly number[],
  theme: string = DEFAULT_HEADER_THEME,
): HeaderScenePair[] {
  const mobile = new Set(mobileSequences);
  return desktopSequences
    .filter((sequence) => mobile.has(sequence))
    .sort((a, b) => a - b)
    .map((sequence) => {
      const suffix = String(sequence).padStart(2, "0");
      return {
        sequence,
        desktop: `${HEADER_SCENE_RUNTIME_BASE}/desk-${theme}-${suffix}.png`,
        mobile: `${HEADER_SCENE_RUNTIME_BASE}/mob-${theme}-${suffix}.png`,
      };
    });
}

export function utcDayIndex(date: Date): number {
  const utcMidnight = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  return Math.floor((utcMidnight - HEADER_SCENE_EPOCH_MS) / 86_400_000);
}

export function resolveHeaderTheme(
  requestedTheme: string,
  catalog: HeaderSceneCatalog,
): readonly HeaderScenePair[] {
  const requested = catalog[requestedTheme];
  if (requested && requested.length > 0) return requested;
  return catalog[DEFAULT_HEADER_THEME] ?? [];
}

export function selectHeaderScene(
  date: Date,
  requestedTheme: string = DEFAULT_HEADER_THEME,
  catalog: HeaderSceneCatalog = { basic: BASIC_HEADER_SCENES },
): HeaderScenePair | null {
  const scenes = resolveHeaderTheme(requestedTheme, catalog);
  if (scenes.length === 0) return null;
  return scenes[((utcDayIndex(date) % scenes.length) + scenes.length) % scenes.length];
}
