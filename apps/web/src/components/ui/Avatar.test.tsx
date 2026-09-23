import { describe, expect, it } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import { Avatar } from "./Avatar";

describe("Avatar (TH-0119)", () => {
  it("renders the unchanged initials mark when there is no photo", () => {
    const { container } = render(<Avatar name="Иванова Анна" size={40} />);
    const mark = container.firstElementChild as HTMLElement;

    expect(mark).toHaveTextContent("ИА");
    expect(mark.querySelector("img")).toBeNull();
    expect(mark.className).not.toMatch(/withPhoto/);
    expect(mark).toHaveStyle({ width: "40px", height: "40px", fontSize: "16px" });
  });

  it("renders the photo inside the same round mark when a photo URL is given", () => {
    const { container } = render(
      <Avatar name="Иванова Анна" photoUrl="/api/v1/persons/p1/photo?v=f1" />,
    );
    const mark = container.firstElementChild as HTMLElement;
    const img = mark.querySelector("img");

    expect(img).toHaveAttribute("src", "/api/v1/persons/p1/photo?v=f1");
    expect(mark).not.toHaveTextContent("ИА");
    expect(mark.className).toMatch(/avatar/);
  });

  it("falls back to initials if the photo fails to load", () => {
    const { container } = render(
      <Avatar name="Иванова Анна" photoUrl="/api/v1/persons/p1/photo?v=f1" />,
    );
    fireEvent.error(container.querySelector("img")!);

    expect(container.firstElementChild).toHaveTextContent("ИА");
    expect(container.querySelector("img")).toBeNull();
  });
});
