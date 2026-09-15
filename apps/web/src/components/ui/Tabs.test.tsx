import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Tabs } from "./Tabs";

function ControlledTabs() {
  const [active, setActive] = useState("a");
  return (
    <Tabs
      label="Пример"
      activeId={active}
      onChange={setActive}
      items={[
        { id: "a", label: "Обзор", content: <p>Overview content</p> },
        { id: "b", label: "Участники", content: <p>Members content</p> },
      ]}
    />
  );
}

describe("Tabs", () => {
  it("marks the active tab with aria-selected and shows its panel", () => {
    render(<ControlledTabs />);

    expect(screen.getByRole("tab", { name: "Обзор" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Участники" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    expect(screen.getByText("Overview content")).toBeInTheDocument();
  });

  it("moves focus and selection with ArrowRight/ArrowLeft (roving tabindex)", async () => {
    const user = userEvent.setup();
    render(<ControlledTabs />);

    const first = screen.getByRole("tab", { name: "Обзор" });
    const second = screen.getByRole("tab", { name: "Участники" });
    expect(first).toHaveAttribute("tabindex", "0");
    expect(second).toHaveAttribute("tabindex", "-1");

    first.focus();
    await user.keyboard("{ArrowRight}");

    expect(second).toHaveFocus();
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Members content")).toBeInTheDocument();

    await user.keyboard("{ArrowLeft}");
    expect(first).toHaveFocus();
    expect(first).toHaveAttribute("aria-selected", "true");
  });
});
