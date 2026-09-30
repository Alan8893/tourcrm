import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { stubFetch, renderWithProviders } from "../test/renderWithProviders";
import { PeoplePage } from "./PeoplePage";
import { ImportPage } from "./ImportPage";

/**
 * TH-0118.5 — Import/Export visibility (docs/04-ux/import-export-ui.md
 * §3, §8): Administrator-only, Import under «Люди», Export under
 * «Отчёты», no new global navigation item, direct URLs guarded.
 */

const emptyCollection = { items: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } };

function meResponse(roleCodes: string[]) {
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

async function renderAppAt(path: string, roleCodes: string[]) {
  window.history.pushState({}, "", path);
  const fetchMock = stubFetch([
    { match: "/auth/me", response: meResponse(roleCodes) },
    { match: "/memberships/exports/fields", response: { items: [] } },
    { match: "/persons", response: emptyCollection },
    { match: "/groups", response: emptyCollection },
    { match: "/events", response: emptyCollection },
  ]);
  // App owns a module-level QueryClient; a fresh module keeps another
  // test's cached `/auth/me` (a different role) out of this one.
  vi.resetModules();
  const { App } = await import("../App");
  render(<App />);
  return fetchMock;
}

function requested(fetchMock: ReturnType<typeof stubFetch>, fragment: string) {
  return fetchMock.mock.calls.some(([input]) => String(input).includes(fragment));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("Import / Export — Administrator visibility", () => {
  it("keeps the seven-item global navigation — no Import/Export item", async () => {
    await renderAppAt("/people", ["admin"]);

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Настройки",
    ]);
    expect(within(nav).queryByText(/Импорт|Экспорт/)).not.toBeInTheDocument();
  });

  it("Administrator sees «Импорт» in Люди", async () => {
    await renderAppAt("/people", ["admin"]);

    const importLink = await screen.findByRole("link", { name: "Импорт" });
    expect(importLink).toHaveAttribute("href", "/people/import");
  });

  it("Administrator sees «Экспорт участников» in Отчёты", async () => {
    await renderAppAt("/reports", ["admin"]);

    expect(await screen.findByRole("link", { name: /Экспорт участников/ })).toHaveAttribute(
      "href",
      "/reports/export",
    );
  });

  it("Administrator can open Import directly", async () => {
    await renderAppAt("/people/import", ["admin"]);
    expect(await screen.findByRole("heading", { name: "Импорт участников" })).toBeInTheDocument();
  });

  it("Administrator can open the Export master directly", async () => {
    await renderAppAt("/reports/export", ["admin"]);
    expect(await screen.findByRole("heading", { name: "Экспорт участников" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
  });

  it("Instructor sees Люди without «Импорт»", async () => {
    await renderAppAt("/people", ["instructor"]);

    expect(await screen.findByRole("heading", { name: "Люди" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Импорт" })).not.toBeInTheDocument();
  });
});

describe("Import / Export — direct URLs for non-administrators render 403", () => {
  it.each([
    { role: "instructor", path: "/people/import" },
    { role: "instructor", path: "/reports/export" },
    { role: "member", path: "/people/import" },
    { role: "member", path: "/reports/export" },
    { role: "guardian", path: "/people/import" },
    { role: "guardian", path: "/reports/export" },
  ])("$role → $path", async ({ role, path }) => {
    const fetchMock = await renderAppAt(path, [role]);

    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Импорт участников" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Экспорт участников" })).not.toBeInTheDocument();
    expect(requested(fetchMock, "/memberships/imports")).toBe(false);
    expect(requested(fetchMock, "/memberships/exports")).toBe(false);
  });

  it("multi-role UNION: Instructor + Administrator can open Import", async () => {
    await renderAppAt("/people/import", ["instructor", "admin"]);
    expect(await screen.findByRole("heading", { name: "Импорт участников" })).toBeInTheDocument();
  });
});

describe("People → Импорт contextual action", () => {
  it("opens the Import workflow from the People list", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/persons", response: emptyCollection },
    ]);
    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/import" element={<ImportPage />} />
      </Routes>,
      { route: "/people" },
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole("link", { name: "Импорт" }));

    expect(await screen.findByRole("heading", { name: "Импорт участников" })).toBeInTheDocument();
    expect(screen.getByLabelText("Файл для импорта")).toBeInTheDocument();
  });
});
