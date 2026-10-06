import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { GroupDetailPage } from "./GroupDetailPage";
import { renderWithHistory, renderWithProviders, stubFetch } from "../test/renderWithProviders";

const GROUP = {
  id: "g1",
  club_id: "club-1",
  name: "Ориентирование",
  description: "Начальная группа",
  status: "active",
  valid_from: "2026-01-01T00:00:00Z",
  valid_to: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function emptyCollection() {
  return { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } };
}

function meResponse(roleCode: string) {
  return {
    user: {
      id: "u1",
      login_identifier: "user@example.com",
      status: "active",
      email_verified_at: null,
      person: { first_name: "Тест", last_name: "Пользователь", middle_name: null, birth_date: null },
    },
    role_assignments: [{ role_code: roleCode, club_id: "club-1", scope_type: "all" }],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GroupDetailPage", () => {
  it("shows identity, status and a back link, and switches tabs on click", async () => {
    const fetchMock = stubFetch([
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1/schedule?from=", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.getByText("Активна")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Все группы/ })).toHaveAttribute("href", "/groups");

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Участники" }));
    expect(await screen.findByText("В группе пока нет участников")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Расписание" }));
    expect(await screen.findByText("Пока нет мероприятий")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/groups/g1/schedule?from="))).toBe(true);
  });

  it("shows a 404 error state for a group that does not exist", async () => {
    stubFetch([
      {
        match: "/groups/missing",
        response: { error: { code: "not_found", message: "Group not found", details: {}, request_id: "r1" } },
        status: 404,
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/missing" },
    );

    expect(await screen.findByText("Группа не найдена")).toBeInTheDocument();
  });

  // --- Add participant (TH-0116 / GitHub Issue #150) ----------------------

  it("hides the add-participant control for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Участники" }));
    await screen.findByText("В группе пока нет участников");

    expect(screen.queryByRole("button", { name: "Добавить участника" })).not.toBeInTheDocument();
  });

  it("lets an admin add a participant by searching for a Person and selecting them", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
      {
        match: "/persons?page=1",
        response: {
          items: [{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null, role_codes: [] }],
          pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Участники" }));
    await user.click(await screen.findByRole("button", { name: "Добавить участника" }));
    await user.type(screen.getByLabelText("Поиск человека"), "Ива");
    await user.click(await screen.findByRole("button", { name: "Иванова Анна" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Добавить участника" })).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/groups/g1/members") && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("searches for participants scoped to the group's club, not the whole installation", async () => {
    // TH-0116 / Issue #150 review follow-up: the participant picker must
    // ask the backend for only people eligible for this Group's Club
    // (an active ClubMembership) — server-side, via `club_id` — never
    // fetch every Person and filter client-side. If the request were
    // missing `club_id`, this stub would not match and the test would
    // fail with "No stub registered".
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
      {
        match: "/persons?page=1&page_size=20&club_id=club-1",
        response: {
          items: [{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null, role_codes: [] }],
          pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Участники" }));
    await user.click(await screen.findByRole("button", { name: "Добавить участника" }));
    await user.type(screen.getByLabelText("Поиск человека"), "Ива");

    expect(await screen.findByRole("button", { name: "Иванова Анна" })).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("club_id=club-1")),
    ).toBe(true);
  });
});

describe("GroupDetailPage — Participant Export contextual action (TH-0118.5)", () => {
  function renderGroup(roleCode: string) {
    stubFetch([
      { match: "/auth/me", response: meResponse(roleCode) },
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1/schedule?from=", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
    ]);
    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );
  }

  it("offers Administrator an export of this Group's participants, and no Group import", async () => {
    renderGroup("admin");

    expect(await screen.findByRole("link", { name: "Экспорт участников" })).toHaveAttribute(
      "href",
      "/reports/export?context=group&group_id=g1",
    );
    expect(screen.queryByRole("link", { name: /Импорт/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Импорт/ })).not.toBeInTheDocument();
  });

  it.each([["instructor"], ["member"]])("does not offer the export to %s", async (role) => {
    renderGroup(role);

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Экспорт участников" })).not.toBeInTheDocument();
  });
});

// --- Issue #282: Member Group Detail ----------------------------------------

describe("GroupDetailPage — Member (Issue #282)", () => {
  const NOT_FOUND = {
    response: { error: { code: "group_not_found", message: "Group not found", details: {}, request_id: "r1" } },
    status: 404,
  };

  function renderAs(roleCode: string, initialEntries: Array<string | { pathname: string; state: unknown }> = ["/groups/g1"]) {
    stubFetch([
      { match: "/auth/me", response: meResponse(roleCode) },
      ...(roleCode === "member"
        ? [
            { match: "/groups/g1/members", ...NOT_FOUND },
            { match: "/groups/g1/schedule?from=", ...NOT_FOUND },
          ]
        : [
            { match: "/groups/g1/members", response: emptyCollection() },
            { match: "/groups/g1/schedule?from=", response: emptyCollection() },
          ]),
      { match: "/groups/g1", response: GROUP },
    ]);
    return renderWithHistory(
      <Routes>
        <Route path="/groups" element={<p>groups list</p>} />
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { initialEntries },
    );
  }

  it("shows a Member the group without any group.manage action", async () => {
    renderAs("member");

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.getByText("Начальная группа")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Экспорт участников" })).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Участники" }));
    expect(await screen.findByText("Список участников этой группы вам недоступен.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить участника" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Расписание" }));
    expect(await screen.findByText("Расписание этой группы вам недоступно.")).toBeInTheDocument();
  });

  it("keeps the back link on a direct Group Detail entry and never redirects away", async () => {
    const { router } = renderAs("member");

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Все группы/ })).toHaveAttribute("href", "/groups");
    expect(router.state.location.pathname).toBe("/groups/g1");
  });

  it("drops the back-to-list link when reached through the single-group shortcut", async () => {
    const { router } = renderAs("member", [{ pathname: "/groups/g1", state: { groupsShortcut: true } }]);

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Все группы/ })).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups/g1");
  });

  it("still offers Administrator the archive action", async () => {
    renderAs("admin");

    expect(await screen.findByRole("button", { name: "Архивировать" })).toBeInTheDocument();
  });

  it("does not offer an Instructor the archive action", async () => {
    renderAs("instructor");

    expect(await screen.findByRole("heading", { name: "Ориентирование" })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Участники" }));
    expect(await screen.findByText("В группе пока нет участников")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
  });
});
