import { describe, expect, it } from "vitest";

import { ICON_SOURCES, illustrationPath } from "./icons";
import { NAVIGATION_ITEMS } from "../shell/navigation";

describe("icon registry", () => {
  it("resolves every navigation item to a WebP path (no SVG, no missing entry)", () => {
    expect(NAVIGATION_ITEMS).toHaveLength(8);
    for (const item of NAVIGATION_ITEMS) {
      const resolve = ICON_SOURCES[item.icon];
      expect(resolve).toBeTypeOf("function");
      const path = resolve(24);
      expect(path).toMatch(/\.webp$/);
      expect(path).not.toMatch(/\.svg$/);
      expect(path.startsWith("/assets/ui/icons/navigation/")).toBe(true);
    }
  });

  it("resolves «Склад» to the approved nav-inventory WebP set", () => {
    for (const size of [16, 20, 24, 32, 48, 64] as const) {
      expect(ICON_SOURCES["nav.inventory"](size)).toBe(
        `/assets/ui/icons/navigation/inventory/inventory_${size}px.webp`,
      );
    }
  });

  it("resolves the approved empty-inventory illustration", () => {
    for (const size of [32, 64, 128, 256] as const) {
      expect(illustrationPath("empty-inventory", size)).toBe(
        `/assets/ui/illustrations/empty-inventory_${size}px.webp`,
      );
    }
  });
});
