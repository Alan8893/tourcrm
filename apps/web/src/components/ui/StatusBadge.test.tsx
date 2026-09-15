import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("always renders both an icon and visible text — never color alone", () => {
    const { container } = render(<StatusBadge status="status.completed" label="Завершено" />);

    expect(screen.getByText("Завершено")).toBeInTheDocument();
    // The icon is decorative (alt="") since the label already carries the
    // accessible name — query it directly rather than by role.
    const icon = container.querySelector("img");
    expect(icon).toHaveAttribute("src", expect.stringContaining("completed"));
    expect(icon).toHaveAttribute("alt", "");
  });
});
