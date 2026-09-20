import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProfileMenu } from "./ProfileMenu";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

function meResponse() {
  return {
    user: {
      id: "u1",
      login_identifier: "user@example.com",
      status: "active",
      email_verified_at: null,
      person: { first_name: "Анна", last_name: "Иванова", middle_name: null, birth_date: null },
    },
    role_assignments: [{ role_code: "admin", club_id: "club-1", scope_type: "all" }],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProfileMenu", () => {
  it("opens the dropdown on click and shows the Настройки entry point (TH-0109 regression: must remain reachable and fully visible)", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderWithProviders(<ProfileMenu />);
    const user = userEvent.setup();

    expect(screen.queryByRole("menu", { name: "Профиль" })).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));

    const menu = screen.getByRole("menu", { name: "Профиль" });
    expect(menu).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Настройки/ })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("closes when clicking outside the menu", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderWithProviders(
      <div>
        <ProfileMenu />
        <button type="button">Снаружи</button>
      </div>,
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));
    expect(screen.getByRole("menu", { name: "Профиль" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Снаружи" }));
    expect(screen.queryByRole("menu", { name: "Профиль" })).not.toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderWithProviders(<ProfileMenu />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));
    expect(screen.getByRole("menu", { name: "Профиль" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Профиль" })).not.toBeInTheDocument();
  });

  it("shows a neutral Гость state for an unauthenticated visitor", async () => {
    stubFetch([{ match: "/auth/me", response: {}, status: 401 }]);
    renderWithProviders(<ProfileMenu />);

    expect(await screen.findByRole("button", { name: /Гость/ })).toBeInTheDocument();
  });
});
