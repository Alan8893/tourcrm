import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeaderScene } from "./HeaderScene";

// Vitest mocks `.css` imports as empty objects, so the CSS rules below are
// asserted against the raw source file instead of an import.
const css = readFileSync(join(process.cwd(), "src/shell/HeaderScene.module.css"), "utf-8");

function ruleBody(selector: string): string {
  const match = css.match(new RegExp(`${selector}\\s*{([^}]*)}`));
  if (!match) throw new Error(`rule ${selector} not found in HeaderScene.module.css`);
  return match[1];
}

describe("HeaderScene", () => {
  it("renders the selected desktop asset and mobile source as decorative content", () => {
    render(<HeaderScene date={new Date("2026-01-01T12:00:00Z")} />);

    const picture = document.querySelector("picture");
    expect(picture).toHaveAttribute("aria-hidden", "true");

    const image = screen.getByAltText("");
    expect(image).toHaveAttribute("src", "/assets/ui/header-scenes-v1/desk-basic-01.png");
    expect(image).toHaveAttribute("alt", "");

    // Responsive source selection (desktop <img src> vs mobile <source srcset>) must be preserved.
    expect(picture?.querySelector("source")).toHaveAttribute(
      "srcset",
      "/assets/ui/header-scenes-v1/mob-basic-01.png",
    );
  });

  it("keeps the decorative scene out of pointer/focus interaction", () => {
    render(<HeaderScene date={new Date("2026-01-01T12:00:00Z")} />);

    const image = screen.getByAltText("");
    const picture = document.querySelector("picture");

    expect(ruleBody("\\.scene")).toMatch(/pointer-events:\s*none/);
    expect(ruleBody("\\.image")).toMatch(/pointer-events:\s*none/);
    expect(picture).toHaveAttribute("aria-hidden", "true");
    expect(image).not.toHaveAttribute("tabindex");
  });

  it("does not crop the artwork with object-fit: cover", () => {
    expect(css).not.toMatch(/object-fit:\s*cover/);
  });

  it("does not downscale the artwork to the Header height via max-height", () => {
    expect(ruleBody("\\.image")).not.toMatch(/max-height/);
    expect(ruleBody("\\.image")).not.toMatch(/height:\s*auto/);
  });

  it("renders the artwork at its authored pixel scale via object-fit: none", () => {
    expect(ruleBody("\\.image")).toMatch(/object-fit:\s*none/);
  });

  it("defines distinct desktop and mobile crop geometry", () => {
    const mobileBlockMatch = css.match(/@media \(max-width: 767\.98px\)\s*{\s*\.image\s*{([^}]*)}/);
    expect(mobileBlockMatch, "expected a mobile .image override block").not.toBeNull();

    const desktopPosition = ruleBody("\\.image").match(/object-position:\s*([^;]+);/)?.[1]?.trim();
    const mobilePosition = mobileBlockMatch?.[1].match(/object-position:\s*([^;]+);/)?.[1]?.trim();

    expect(desktopPosition).toBeTruthy();
    expect(mobilePosition).toBeTruthy();
    expect(mobilePosition).not.toBe(desktopPosition);
  });
});
