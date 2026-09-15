import { describe, expect, it } from "vitest";

import { ICON_SOURCES } from "./icons";
import { NAVIGATION_ITEMS } from "../shell/navigation";

describe("icon registry", () => {
  it("resolves every navigation item to a WebP path (no SVG, no missing entry)", () => {
    expect(NAVIGATION_ITEMS).toHaveLength(7);
    for (const item of NAVIGATION_ITEMS) {
      const resolve = ICON_SOURCES[item.icon];
      expect(resolve).toBeTypeOf("function");
      const path = resolve(24);
      expect(path).toMatch(/\.webp$/);
      expect(path).not.toMatch(/\.svg$/);
      expect(path.startsWith("/assets/ui/icons/navigation/")).toBe(true);
    }
  });
});
