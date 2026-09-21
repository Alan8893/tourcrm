import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { GroupDetailPage } from "./GroupDetailPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

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
});
