import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";

import { App } from "./App";
import { stubFetch } from "./test/renderWithProviders";

function meResponse(roleCodes: string[] = ["admin"]) {
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
    role_assignments: roleCodes.map((role_code) => ({ role_code, club_id: "club-1", scope_type: "all" })),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("renders exactly the eight approved navigation items, the brand logo and a profile area, and lands on Home", async () => {
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
      "Склад",
      "Настройки",
    ];
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(8);
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

describe("App — direct navigation to role-hidden sections (Issue #212)", () => {
  const emptyCollection = { items: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } };

  async function renderAt(path: string, roleCodes: string[]) {
    window.history.pushState({}, "", path);
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(roleCodes) },
      { match: "/persons", response: emptyCollection },
      { match: "/groups", response: emptyCollection },
    ]);
    // App owns a module-level QueryClient; a fresh module keeps another
    // test's cached `/auth/me` (a different role) out of this one.
    vi.resetModules();
    const { App: FreshApp } = await import("./App");
    render(<FreshApp />);
    return fetchMock;
  }

  function requested(fetchMock: ReturnType<typeof stubFetch>, fragment: string) {
    return fetchMock.mock.calls.some(([input]) => String(input).includes(fragment));
  }

  it.each([
    { name: "Member → /people", roles: ["member"], path: "/people", fragment: "/persons" },
    { name: "Member → /people/:id", roles: ["member"], path: "/people/p9", fragment: "/persons" },
    { name: "Guardian → /people", roles: ["guardian"], path: "/people", fragment: "/persons" },
    { name: "Guardian → /groups", roles: ["guardian"], path: "/groups", fragment: "/groups" },
    { name: "Instructor → /reports", roles: ["instructor"], path: "/reports", fragment: "/reports" },
    { name: "Member → /reports", roles: ["member"], path: "/reports", fragment: "/reports" },
    { name: "Guardian → /reports", roles: ["guardian"], path: "/reports", fragment: "/reports" },
  ])("$name renders the 403 state, not the section's page or its management UI", async ({ roles, path, fragment }) => {
    const fetchMock = await renderAt(path, roles);

    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
    // The shell (navigation) stays; the hidden section's page does not render.
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить человека" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Создать группу/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/появится в одном из следующих этапов/)).not.toBeInTheDocument();
    expect(requested(fetchMock, fragment)).toBe(false);
  });

  it("Guardian → /groups stays forbidden: there is no Guardian Groups section (ADR-0046)", async () => {
    const fetchMock = await renderAt("/groups", ["guardian"]);

    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).queryByRole("link", { name: "Группы" })).not.toBeInTheDocument();
    expect(requested(fetchMock, "/groups")).toBe(false);
  });

  it("Administrator can open Reports directly", async () => {
    await renderAt("/reports", ["admin"]);

    expect(await screen.findByRole("heading", { name: "Отчёты" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
  });

  it.each([["instructor"], ["member"], ["guardian"]])(
    "%s can open Achievements directly (visible although not implemented yet)",
    async (role) => {
      await renderAt("/achievements", [role]);

      expect(await screen.findByRole("heading", { name: "Достижения" })).toBeInTheDocument();
      expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
    },
  );

  it("Member can open Groups directly", async () => {
    await renderAt("/groups", ["member"]);

    expect(await screen.findByRole("heading", { name: "Группы" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
  });

  it("multi-role UNION: Member + Instructor can open People directly", async () => {
    const fetchMock = await renderAt("/people", ["member", "instructor"]);

    expect(await screen.findByRole("heading", { name: "Люди" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
    await waitFor(() => expect(requested(fetchMock, "/persons")).toBe(true));
  });

  it("multi-role UNION: Guardian + Administrator can open Reports directly", async () => {
    await renderAt("/reports", ["guardian", "admin"]);

    expect(await screen.findByRole("heading", { name: "Отчёты" })).toBeInTheDocument();
  });
});

describe("App — Guardian child-to-group context (ADR-0046, Issue #301)", () => {
  const group = {
    id: "g1",
    club_id: "club-1",
    name: "Туристы-5",
    description: "Описание группы",
    status: "active",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  const emptyCollection = { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } };

  async function renderAt(path: string, roleCodes: string[]) {
    window.history.pushState({}, "", path);
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(roleCodes) },
      { match: "/groups/g1/schedule", response: emptyCollection },
      { match: "/groups/g1/members", response: emptyCollection },
      { match: "/groups/g1", response: group },
      {
        match: "/me/children",
        response: {
          items: [
            {
              id: "c1",
              last_name: "Иванов",
              first_name: "Иван",
              middle_name: null,
              birth_date: null,
              photo_file_id: null,
              groups: [{ id: "g1", name: "Туристы-5" }],
            },
          ],
          pagination: { page: 1, page_size: 1, total: 1, pages: 1 },
        },
      },
      { match: "/news", response: emptyCollection },
      { match: "/events", response: emptyCollection },
    ]);
    vi.resetModules();
    const { App: FreshApp } = await import("./App");
    render(<FreshApp />);
    return fetchMock;
  }

  function urls(fetchMock: ReturnType<typeof stubFetch>) {
    return fetchMock.mock.calls.map(([input]) => String(input));
  }

  it("Guardian opens a child's Group from «Мои дети» and sees only its schedule", async () => {
    const fetchMock = await renderAt("/", ["guardian"]);

    const groups = await screen.findByRole("list", { name: "Группы: Иванов Иван" });
    within(groups).getByRole("link", { name: "Туристы-5" }).click();

    expect(await screen.findByRole("heading", { name: "Туристы-5" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Расписание"]);
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
    // Back goes Home (where «Мои дети» lives), not to a Groups list.
    const main = screen.getByRole("main");
    expect(within(main).getByRole("link", { name: /Главная/ })).toHaveAttribute("href", "/");
    expect(within(main).queryByRole("link", { name: /Все группы/ })).not.toBeInTheDocument();
    // The Group Detail is authorized by the backend item endpoint; no
    // Groups list or roster is ever requested for Guardian.
    await waitFor(() => expect(urls(fetchMock)).toContain("/api/v1/groups/g1"));
    expect(urls(fetchMock).some((url) => /\/groups(\?|$)/.test(url))).toBe(false);
    expect(urls(fetchMock).some((url) => url.includes("/members"))).toBe(false);
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).queryByRole("link", { name: "Группы" })).not.toBeInTheDocument();
  });

  it("shows the backend's existence-hiding 404 for a Group the Guardian may not read", async () => {
    window.history.pushState({}, "", "/groups/foreign");
    stubFetch([
      { match: "/auth/me", response: meResponse(["guardian"]) },
      {
        match: "/groups/foreign",
        status: 404,
        response: { error: { code: "group_not_found", message: "Group not found", details: {}, request_id: "r" } },
      },
    ]);
    vi.resetModules();
    const { App: FreshApp } = await import("./App");
    render(<FreshApp />);

    // App's QueryClient retries a failed query once before the error state.
    expect(await screen.findByText("Группа не найдена", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("Member keeps the «Все группы» back link to the Groups section", async () => {
    await renderAt("/groups/g1", ["member"]);

    expect(await screen.findByRole("heading", { name: "Туристы-5" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: /Все группы/ })).toHaveAttribute(
      "href",
      "/groups",
    );
  });
});
