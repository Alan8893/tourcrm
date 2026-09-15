import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GroupDetailPage", () => {
  it("shows identity, status and a back link, and switches tabs on click", async () => {
    stubFetch([
      { match: "/groups/g1/members", response: emptyCollection() },
      { match: "/groups/g1/schedule", response: emptyCollection() },
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
});
