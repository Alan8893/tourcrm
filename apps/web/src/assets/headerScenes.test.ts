import { describe, expect, it } from "vitest";

import {
  BASIC_HEADER_SCENES,
  HEADER_SCENE_EPOCH_MS,
  completeHeaderScenePairs,
  resolveHeaderTheme,
  selectHeaderScene,
  utcDayIndex,
} from "./headerScenes";

describe("Header scene selector", () => {
  it("starts at scene 01 on the canonical epoch", () => {
    const epoch = new Date(HEADER_SCENE_EPOCH_MS);
    expect(utcDayIndex(epoch)).toBe(0);
    expect(selectHeaderScene(epoch)?.sequence).toBe(1);
  });

  it("keeps the same scene throughout a UTC calendar day", () => {
    const morning = new Date("2026-09-17T00:01:00Z");
    const evening = new Date("2026-09-17T23:59:59Z");
    expect(selectHeaderScene(morning)).toEqual(selectHeaderScene(evening));
  });

  it("advances and wraps cyclically", () => {
    const first = new Date("2026-01-01T00:00:00Z");
    const ninth = new Date("2026-01-09T12:00:00Z");
    const tenth = new Date("2026-01-10T00:00:00Z");
    expect(selectHeaderScene(first)?.sequence).toBe(1);
    expect(selectHeaderScene(ninth)?.sequence).toBe(9);
    expect(selectHeaderScene(tenth)?.sequence).toBe(1);
  });

  it("is independent of the user's timezone", () => {
    const utc = new Date("2026-03-17T12:00:00Z");
    const sameInstantWithOffset = new Date("2026-03-17T15:00:00+03:00");
    expect(selectHeaderScene(utc)).toEqual(selectHeaderScene(sameInstantWithOffset));
  });

  it("builds only complete desktop/mobile pairs in numeric order", () => {
    const pairs = completeHeaderScenePairs([9, 2, 4, 1], [4, 1, 9]);
    expect(pairs.map((pair) => pair.sequence)).toEqual([1, 4, 9]);
    expect(pairs[0].desktop).toContain("desk-basic-01.png");
    expect(pairs[0].mobile).toContain("mob-basic-01.png");
  });

  it("falls back to basic when the requested theme is unavailable", () => {
    const catalog = { basic: BASIC_HEADER_SCENES, winter: [] };
    expect(resolveHeaderTheme("winter", catalog)).toEqual(BASIC_HEADER_SCENES);
    expect(resolveHeaderTheme("missing", catalog)).toEqual(BASIC_HEADER_SCENES);
  });

  it("returns null when even the basic fallback has no usable pairs", () => {
    expect(selectHeaderScene(new Date("2026-01-01T00:00:00Z"), "winter", { basic: [] })).toBeNull();
  });
});
