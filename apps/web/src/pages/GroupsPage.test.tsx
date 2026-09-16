import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GroupsPage } from "./GroupsPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

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
      { match: "/auth/me", response: ME_RESPONSE },
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
