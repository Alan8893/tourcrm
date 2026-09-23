import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { App } from "./App";
import { stubFetch } from "./test/renderWithProviders";

function meResponse() {
  return {
    user: {
      id: "u1",
      login_identifier: "user@example.com",
      status: "active",
      email_verified_at: null,
      person: {
        id: "p1",
        first_name: "Анна",
        last_name: "Иванова",
        middle_name: null,
        birth_date: null,
        photo_file_id: null,
      },
    },
    role_assignments: [{ role_code: "admin", club_id: "club-1", scope_type: "all" }],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("renders exactly the seven approved navigation items, the brand logo and a profile area, and lands on Home", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/events",
        response: { items: [], pagination: { page: 1, page_size: 5, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    const expectedItems = [
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Настройки",
    ];
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(7);
    expectedItems.forEach((label) => {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    });

    expect(screen.getByAltText("TourCRM «Вектор»")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Иванова Анна/ })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /Иванова Анна/ })).toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("gives the current page an accessible current-page marker", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/events",
        response: { items: [], pagination: { page: 1, page_size: 5, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    const homeLink = await screen.findByRole("link", { name: "Главная" });
    expect(homeLink).toHaveAttribute("aria-current", "page");
  });

  it("sends an unauthenticated visitor (401 from /auth/me) to /login instead of rendering the shell", async () => {
    window.history.pushState({}, "", "/groups");
    stubFetch([{ match: "/auth/me", response: {}, status: 401 }]);

    render(<App />);

    expect(await screen.findByRole("button", { name: "Войти" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(new URLSearchParams(window.location.search).get("next")).toBe("/groups");
    expect(screen.queryByRole("navigation", { name: "Основная навигация" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("renders the real People Core screen at /people, not PlaceholderPage (TH-0094 regression)", async () => {
    window.history.pushState({}, "", "/people");
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/persons",
        response: { items: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Люди" })).toBeInTheDocument();
    expect(screen.queryByText(/появится в одном из следующих этапов/)).not.toBeInTheDocument();
  });
});
