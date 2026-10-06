import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

import { GroupsPage } from "./GroupsPage";
import { renderWithHistory, renderWithProviders, stubFetch } from "../test/renderWithProviders";

const ME_RESPONSE = {
  user: {
    id: "u1",
    login_identifier: "leader@example.com",
    status: "active",
    email_verified_at: null,
    person: { first_name: "Анна", last_name: "Иванова", middle_name: null, birth_date: null },
  },
  role_assignments: [{ role_code: "instructor", club_id: "club-1", scope_type: "all" }],
};

function meResponse(roleCodes: string[]) {
  return {
    ...ME_RESPONSE,
    role_assignments: roleCodes.map((role_code) => ({ role_code, club_id: "club-1", scope_type: "all" })),
  };
}

function group(id: string, name: string, status: "active" | "archived" = "active") {
  return {
    id,
    club_id: "club-1",
    name,
    description: null,
    status,
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function groupsResponse(items: unknown[]) {
  return { items, pagination: { page: 1, page_size: 50, total: items.length, pages: 1 } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GroupsPage", () => {
  it("shows an empty state with the empty-groups illustration when there are no groups", async () => {
    stubFetch([
      { match: "/auth/me", response: ME_RESPONSE },
      { match: "/groups", response: groupsResponse([]) },
    ]);

    renderWithProviders(<GroupsPage />);

    expect(await screen.findByText("Пока нет ни одной группы")).toBeInTheDocument();
  });

  it("renders a readable group list with status badges and filters it by search", async () => {
    stubFetch([
      { match: "/auth/me", response: ME_RESPONSE },
      {
        match: "/groups",
        response: groupsResponse([
          {
            id: "g1",
            club_id: "club-1",
            name: "Ориентирование",
            description: "Начальная группа",
            status: "active",
            valid_from: "2026-01-01T00:00:00Z",
            valid_to: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
          {
            id: "g2",
            club_id: "club-1",
            name: "Скалолазание",
            description: null,
            status: "archived",
            valid_from: "2025-01-01T00:00:00Z",
            valid_to: "2025-12-31T00:00:00Z",
            created_at: "2025-01-01T00:00:00Z",
            updated_at: "2025-01-01T00:00:00Z",
          },
        ]),
      },
    ]);

    renderWithProviders(<GroupsPage />);

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Скалолазание" })).toBeInTheDocument();
    expect(screen.getByText("Активна")).toBeInTheDocument();
    expect(screen.getByText("Архивная")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Поиск по названию"), "Скал");

    expect(screen.queryByRole("link", { name: "Ориентирование" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Скалолазание" })).toBeInTheDocument();
  });

  it("closes the create dialog after a successful group creation", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/groups", response: groupsResponse([]) },
      {
        match: "/groups",
        response: {
          id: "g1",
          club_id: "club-1",
          name: "Новая группа",
          description: null,
          status: "active",
          valid_from: "2026-09-16T00:00:00Z",
          valid_to: null,
          created_at: "2026-09-16T00:00:00Z",
          updated_at: "2026-09-16T00:00:00Z",
        },
      },
    ]);

    renderWithProviders(<GroupsPage />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать группу" }));
    expect(screen.getByRole("dialog", { name: "Новая группа" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Название"), "Новая группа");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Новая группа" })).not.toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalled();
  });

  it("shows an error state when the groups request fails", async () => {
    stubFetch([
      { match: "/auth/me", response: ME_RESPONSE },
      {
        match: "/groups",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(<GroupsPage />);

    expect(await screen.findByText("Не удалось загрузить группы")).toBeInTheDocument();
  });
});

// --- Issue #282: role-aware Groups landing (Member single-group shortcut) ---

/** Stand-in for GroupDetailPage: shows where the router landed and the
 * location state the shortcut passed along. */
function DetailProbe() {
  const location = useLocation();
  const state = location.state as { groupsShortcut?: boolean } | null;
  return (
    <p>
      detail:{location.pathname}:{state?.groupsShortcut ? "shortcut" : "direct"}
    </p>
  );
}

function renderGroupsSection(initialEntries = ["/", "/groups"]) {
  return renderWithHistory(
    <Routes>
      <Route path="/" element={<p>home</p>} />
      <Route path="/groups" element={<GroupsPage />} />
      <Route path="/groups/:groupId" element={<DetailProbe />} />
    </Routes>,
    { initialEntries },
  );
}

function groupsListCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.filter(([input]) => /\/groups(\?|$)/.test(String(input))).length;
}

describe("GroupsPage — Member landing (Issue #282)", () => {
  it("Member with zero groups sees the empty state and stays on the list", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/groups", response: groupsResponse([]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByText("Пока нет ни одной группы")).toBeInTheDocument();
    expect(screen.getByText("Здесь появятся доступные вам группы.")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
  });

  it("Member with exactly one group is taken straight to its detail page, replacing the list entry", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByText("detail:/groups/g1:shortcut")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Группы" })).not.toBeInTheDocument();
    expect(groupsListCalls(fetchMock)).toBe(1);

    // No redirect loop and predictable Back: the list entry was replaced,
    // so Back leaves the Groups section instead of re-entering it.
    await act(async () => {
      await router.navigate(-1);
    });
    expect(router.state.location.pathname).toBe("/");
    expect(await screen.findByText("home")).toBeInTheDocument();
    expect(groupsListCalls(fetchMock)).toBe(1);
  });

  it("Member with two or more groups sees the normal list", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/groups",
        response: groupsResponse([group("g1", "Ориентирование"), group("g2", "Скалолазание")]),
      },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toHaveAttribute("href", "/groups/g1");
    expect(screen.getByRole("link", { name: "Скалолазание" })).toHaveAttribute("href", "/groups/g2");
    expect(router.state.location.pathname).toBe("/groups");
  });

  it("does not redirect (or show the list) before the groups query resolves", async () => {
    let resolveGroups: (response: Response) => void = () => undefined;
    const pendingGroups = new Promise<Response>((resolve) => {
      resolveGroups = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/auth/me")) {
          return new Response(JSON.stringify(meResponse(["member"])), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return pendingGroups;
      }),
    );

    const { router } = renderGroupsSection();

    expect(await screen.findByText("Загружаем группы…")).toBeInTheDocument();
    // `/auth/me` has resolved by now; still nothing but the loading state.
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
    expect(router.state.location.pathname).toBe("/groups");
    expect(screen.queryByRole("heading", { name: "Группы" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Поиск по названию")).not.toBeInTheDocument();

    await act(async () => {
      resolveGroups(
        new Response(JSON.stringify(groupsResponse([group("g1", "Ориентирование")])), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });

    expect(await screen.findByText("detail:/groups/g1:shortcut")).toBeInTheDocument();
  });

  it("never redirects to an archived group", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование", "archived")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
  });

  it("leaves a direct Group Detail entry alone", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection(["/groups/g2"]);

    expect(await screen.findByText("detail:/groups/g2:direct")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups/g2");
    expect(groupsListCalls(fetchMock)).toBe(0);
  });

  it("hides group management actions from a Member", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/groups",
        response: groupsResponse([group("g1", "Ориентирование"), group("g2", "Скалолазание")]),
      },
    ]);

    renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Создать группу" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
  });

  it("Admin with exactly one group keeps the normal list and management actions", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
    expect(screen.getByRole("button", { name: "Создать группу" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Архивировать" })).toBeInTheDocument();
  });

  it("Instructor with exactly one group keeps the normal list without group.manage actions", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["instructor"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
    expect(screen.queryByRole("button", { name: "Создать группу" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архивировать" })).not.toBeInTheDocument();
  });

  it("Member + Instructor with exactly one group is taken straight to its detail page", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member", "instructor"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByText("detail:/groups/g1:shortcut")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups/g1");
  });

  it("Member + Instructor with two or more groups sees the normal list", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member", "instructor"]) },
      {
        match: "/groups",
        response: groupsResponse([group("g1", "Ориентирование"), group("g2", "Скалолазание")]),
      },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Скалолазание" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
  });

  it("Member + Instructor with zero groups sees the empty state", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member", "instructor"]) },
      { match: "/groups", response: groupsResponse([]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByText("Пока нет ни одной группы")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
  });

  it("Admin + Member with exactly one group keeps the normal Admin list", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["admin", "member"]) },
      { match: "/groups", response: groupsResponse([group("g1", "Ориентирование")]) },
    ]);

    const { router } = renderGroupsSection();

    expect(await screen.findByRole("link", { name: "Ориентирование" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/groups");
    expect(screen.getByRole("button", { name: "Создать группу" })).toBeInTheDocument();
  });
});
