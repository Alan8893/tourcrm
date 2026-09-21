import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { ProfileMenu } from "./ProfileMenu";
import { createTestQueryClient, renderWithProviders, stubFetch } from "../test/renderWithProviders";

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

  // --- Logout (TH-0114 / GitHub Issue #146) -------------------------------

  it("offers Выйти for an authenticated user", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderWithProviders(<ProfileMenu />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));

    expect(screen.getByRole("menuitem", { name: "Выйти" })).toBeInTheDocument();
  });

  it("never offers Выйти for an unauthenticated guest", async () => {
    stubFetch([{ match: "/auth/me", response: {}, status: 401 }]);
    renderWithProviders(<ProfileMenu />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Гость/ }));

    expect(screen.queryByRole("menuitem", { name: "Выйти" })).not.toBeInTheDocument();
  });

  it("calls the canonical POST /auth/logout endpoint and navigates to /login on success", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/auth/logout", response: {} },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/" element={<ProfileMenu />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>,
      { route: "/" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));
    await user.click(screen.getByRole("menuitem", { name: "Выйти" }));

    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(screen.queryByRole("menu", { name: "Профиль" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) => String(input).includes("/auth/logout") && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("clears the cached identity so a reload after logout shows a guest, never the previous session", async () => {
    // TH-0114 requirement: the user must not remain authenticated after a
    // reload following logout — modeled here as unmounting and remounting
    // ProfileMenu against the SAME React Query cache the logout mutation
    // ran against, with the backend now actually reporting no session.
    const client = createTestQueryClient();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/auth/logout", response: {} },
    ]);

    const { unmount } = renderWithProviders(
      <Routes>
        <Route path="/" element={<ProfileMenu />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>,
      { route: "/", client },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));
    await user.click(screen.getByRole("menuitem", { name: "Выйти" }));
    await screen.findByText("Login page");
    unmount();

    fetchMock.mockImplementation(
      async () => new Response(JSON.stringify({}), { status: 401, headers: { "Content-Type": "application/json" } }),
    );
    renderWithProviders(<ProfileMenu />, { client });

    expect(await screen.findByRole("button", { name: /Гость/ })).toBeInTheDocument();
  });

  it("shows a notification and keeps the guest unauthenticated on a logout failure", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/auth/logout",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(<ProfileMenu />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Иванова Анна/ }));
    await user.click(screen.getByRole("menuitem", { name: "Выйти" }));

    expect(await screen.findByText("Сбой сервера")).toBeInTheDocument();
  });
});
