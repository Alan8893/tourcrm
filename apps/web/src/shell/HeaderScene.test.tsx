import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HeaderScene } from "./HeaderScene";

describe("HeaderScene", () => {
  it("renders the selected desktop asset and mobile source as decorative content", () => {
    render(<HeaderScene date={new Date("2026-01-01T12:00:00Z")} />);

    const picture = document.querySelector("picture");
    expect(picture).toHaveAttribute("aria-hidden", "true");

    const image = screen.getByAltText("");
    expect(image).toHaveAttribute("src", "/assets/ui/header-scenes-v1/desk-basic-01.png");
    expect(image).toHaveAttribute("alt", "");

    expect(picture?.querySelector("source")).toHaveAttribute(
      "srcset",
      "/assets/ui/header-scenes-v1/mob-basic-01.png",
    );
  });
});
