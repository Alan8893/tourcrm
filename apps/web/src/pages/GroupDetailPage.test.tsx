import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
      { match: "/auth/me", response: meResponse("admin") },
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
      { match: "/auth/me", response: meResponse("instructor") },
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
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Экспорт участников" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить участника" })).not.toBeInTheDocument();
    // Issue #285: the schedule is the Member's only tab; a backend denial
    // is still shown as "no access", never as a failure.
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

// --- Issue #285: role-aware tabs and schedule presentation --------------------

describe("GroupDetailPage — role-aware tabs (Issue #285)", () => {
  const SCHEDULE = {
    items: [
      {
        id: "e1",
        kind: "event",
        club_id: "club-1",
        event_type: "lesson",
        title: "Тренировка",
        description: null,
        start_at: new Date(2026, 2, 17, 18, 0).toISOString(),
        end_at: new Date(2026, 2, 17, 19, 30).toISOString(),
        timezone: "Europe/Moscow",
        status: "published",
        cancellation_reason: null,
        series_id: null,
        series_version: null,
      },
    ],
    pagination: { page: 1, page_size: 50, total: 1, pages: 1 },
  };

  function renderAs(roleCodes: string[]) {
    const fetchMock = stubFetch([
      {
        match: "/auth/me",
        response: {
          ...meResponse(roleCodes[0]),
          role_assignments: roleCodes.map((role_code) => ({ role_code, club_id: "club-1", scope_type: "all" })),
        },
      },
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1/schedule?from=", response: SCHEDULE },
      { match: "/groups/g1", response: GROUP },
    ]);
    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );
    return fetchMock;
  }

  async function tabNames(): Promise<string[]> {
    await screen.findByRole("heading", { name: "Ориентирование" });
    return screen.getAllByRole("tab").map((tab) => tab.textContent ?? "");
  }

  it("gives Administrator overview, participants and schedule", async () => {
    renderAs(["admin"]);
    expect(await tabNames()).toEqual(["Обзор", "Участники", "Расписание"]);
    expect(screen.getByText("Начальная группа")).toBeInTheDocument();
  });

  it("gives Instructor participants and schedule, opening on participants", async () => {
    renderAs(["instructor"]);
    expect(await tabNames()).toEqual(["Участники", "Расписание"]);
    expect(screen.queryByText("Начальная группа")).not.toBeInTheDocument();
    expect(await screen.findByText("В группе пока нет участников")).toBeInTheDocument();
  });

  it("gives Member only the schedule and never requests the participant list", async () => {
    const fetchMock = renderAs(["member"]);
    expect(await tabNames()).toEqual(["Расписание"]);
    expect(screen.queryByText("Начальная группа")).not.toBeInTheDocument();
    expect(await screen.findByText("Тренировка")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/groups/g1/members"))).toBe(false);
  });

  it("gives a Member who is also an Instructor the union of both", async () => {
    renderAs(["member", "instructor"]);
    expect(await tabNames()).toEqual(["Участники", "Расписание"]);
  });

  it("shows each schedule entry's date, start/end time, title and status", async () => {
    renderAs(["member"]);
    expect(await screen.findByText("17 марта, 18:00–19:30")).toBeInTheDocument();
    expect(screen.getByText("Тренировка")).toBeInTheDocument();
    expect(screen.getByText("Запланировано")).toBeInTheDocument();
  });
});

// --- Issue #286: remove / transfer actions ----------------------------------

describe("GroupDetailPage — membership actions (Issue #286)", () => {
  const ACTIVE = {
    id: "m1",
    group_id: "g1",
    club_membership_id: "cm1",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    membership_status: "active",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  const ENDED = { ...ACTIVE, id: "m0", membership_status: "ended", valid_to: "2025-06-01T00:00:00Z" };
  const group = (id: string, name: string, clubId = "club-1") => ({ ...GROUP, id, name, club_id: clubId });

  function renderAs(roleCode: string) {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(roleCode) },
      { match: "/group-memberships/m1/transfer", response: { ...ACTIVE, id: "m2", group_id: "g2" } },
      { match: "/group-memberships/m1/end", response: { ...ACTIVE, membership_status: "ended" } },
      {
        match: "/groups?status=active",
        response: {
          items: [GROUP, group("g2", "Старшая группа"), group("g3", "Чужой клуб", "club-2")],
          pagination: { page: 1, page_size: 100, total: 3, pages: 1 },
        },
      },
      {
        match: "/groups/g1/members",
        response: { items: [ACTIVE, ENDED], pagination: { page: 1, page_size: 50, total: 2, pages: 1 } },
      },
      { match: "/groups/g1/schedule?from=", response: emptyCollection() },
      { match: "/groups/g1", response: GROUP },
      { match: "/memberships/cm1", response: { id: "cm1", person_id: "p1", club_id: "club-1" } },
      {
        match: "/persons/p1",
        response: { id: "p1", first_name: "Анна", last_name: "Иванова", middle_name: null, birth_date: null },
      },
    ]);
    renderWithProviders(
      <Routes>
        <Route path="/groups/:groupId" element={<GroupDetailPage />} />
      </Routes>,
      { route: "/groups/g1" },
    );
    return fetchMock;
  }

  function mutationCalls(fetchMock: ReturnType<typeof stubFetch>) {
    return fetchMock.mock.calls
      .filter(([, init]) => (init?.method ?? "GET") !== "GET")
      .map(([input, init]) => ({ url: String(input), body: init?.body }));
  }

  async function openParticipants() {
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Участники" }));
    await screen.findAllByText("Иванова Анна");
    return user;
  }

  it("offers Administrator move/remove on the active membership only", async () => {
    renderAs("admin");
    await openParticipants();

    expect(screen.getAllByRole("button", { name: "Переместить" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Удалить" })).toHaveLength(1);
  });

  it("offers an Instructor no membership management actions", async () => {
    renderAs("instructor");
    await openParticipants();

    expect(screen.queryByRole("button", { name: "Переместить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить" })).not.toBeInTheDocument();
  });

  it("removes only after confirmation, with one end request", async () => {
    const fetchMock = renderAs("admin");
    const user = await openParticipants();

    await user.click(screen.getByRole("button", { name: "Удалить" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("История участия сохранится");
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(mutationCalls(fetchMock)).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Удалить" }));
    const confirm = await screen.findByRole("dialog");
    await user.click(within(confirm).getByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
    expect(mutationCalls(fetchMock)[0].url).toContain("/group-memberships/m1/end");
  });

  it("transfers with ONE transfer request to a same-Club active Group, after confirmation", async () => {
    const fetchMock = renderAs("admin");
    const user = await openParticipants();

    await user.click(screen.getByRole("button", { name: "Переместить" }));
    const dialog = await screen.findByRole("dialog");
    const select = await within(dialog).findByLabelText("Целевая группа");
    const options = within(select).getAllByRole("option").map((option) => option.textContent);
    // Not the current Group, not another Club's Group.
    expect(options).toEqual(["Выберите группу", "Старшая группа"]);
    const confirm = within(dialog).getByRole("button", { name: "Переместить" });
    expect(confirm).toBeDisabled();

    await user.selectOptions(select, "g2");
    await user.click(confirm);

    await waitFor(() => expect(mutationCalls(fetchMock)).toHaveLength(1));
    const [call] = mutationCalls(fetchMock);
    expect(call.url).toContain("/group-memberships/m1/transfer");
    expect(JSON.parse(String(call.body))).toEqual({ target_group_id: "g2" });
  });
});
