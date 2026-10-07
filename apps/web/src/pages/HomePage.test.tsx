import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { HomePage } from "./HomePage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

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

function emptyEvents() {
  return { items: [], pagination: { page: 1, page_size: 5, total: 0, pages: 0 } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HomePage — quick actions follow role-aware navigation (Issue #212)", () => {
  it.each([
    { role: "admin", groups: true },
    { role: "instructor", groups: true },
    { role: "member", groups: true },
    { role: "guardian", groups: false },
  ])("$role: «Перейти к группам» shown: $groups", async ({ role, groups }) => {
    stubFetch([
      { match: "/auth/me", response: meResponse(role) },
      { match: "/news", response: emptyEvents() },
      { match: "/me/children", response: { items: [], pagination: { page: 1, page_size: 1, total: 0, pages: 0 } } },
      { match: "/events", response: emptyEvents() },
    ]);

    renderWithProviders(<HomePage />);

    await screen.findByText("Ближайшие события");
    await waitFor(() =>
      expect(Boolean(screen.queryByRole("link", { name: /Перейти к группам/ }))).toBe(groups),
    );
    expect(screen.getByRole("link", { name: /Все события/ })).toBeInTheDocument();
  });
});

describe("HomePage — guardian «Мои дети» section", () => {
  it("does not render the section for a non-guardian role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
    ]);

    renderWithProviders(<HomePage />);

    await screen.findByText("Ближайшие события");
    expect(screen.queryByText("Мои дети")).not.toBeInTheDocument();
  });

  it("shows a loading state while children are being fetched", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      { match: "/me/children", response: { items: [], pagination: { page: 1, page_size: 1, total: 0, pages: 0 } } },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Мои дети")).toBeInTheDocument();
    expect(screen.getByText("Загружаем детей…")).toBeInTheDocument();
  });

  it("renders exactly the safe projection for each child and nothing else", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      {
        match: "/me/children",
        response: {
          items: [
            {
              id: "c1",
              last_name: "Иванова",
              first_name: "Соня",
              middle_name: null,
              birth_date: "2016-03-02",
              photo_file_id: null,
              groups: [],
            },
          ],
          pagination: { page: 1, page_size: 1, total: 1, pages: 1 },
        },
      },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Иванова Соня")).toBeInTheDocument();
    expect(screen.getByText("02.03.2016")).toBeInTheDocument();
    // No contact fields anywhere on the page for this child.
    expect(screen.queryByText(/@/)).not.toBeInTheDocument();
  });

  it("shows an empty state when the guardian has no current children", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      { match: "/me/children", response: { items: [], pagination: { page: 1, page_size: 1, total: 0, pages: 0 } } },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Дети не найдены")).toBeInTheDocument();
  });

  it("shows an error state with a working retry action", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      {
        match: "/me/children",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Не удалось загрузить данные о детях")).toBeInTheDocument();

    const callsBeforeRetry = fetchMock.mock.calls.length;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(callsBeforeRetry);
    });
  });
});

describe("HomePage — «Мои дети» → Group context (ADR-0046, Issue #301)", () => {
  function childrenResponse(items: Array<Record<string, unknown>>) {
    return { items, pagination: { page: 1, page_size: items.length || 1, total: items.length, pages: items.length ? 1 : 0 } };
  }

  function child(id: string, lastName: string, groups: Array<{ id: string; name: string }>) {
    return {
      id,
      last_name: lastName,
      first_name: "Иван",
      middle_name: null,
      birth_date: null,
      photo_file_id: null,
      groups,
    };
  }

  it("shows each child's current Groups as links to the existing Group Detail", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      {
        match: "/me/children",
        response: childrenResponse([
          child("c1", "Иванов", [
            { id: "g1", name: "Туристы-5" },
            { id: "g2", name: "Ориентирование" },
          ]),
          child("c2", "Петров", [{ id: "g2", name: "Ориентирование" }]),
        ]),
      },
    ]);

    renderWithProviders(<HomePage />);

    const ivan = await screen.findByRole("list", { name: "Группы: Иванов Иван" });
    expect(within(ivan).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Туристы-5", "/groups/g1"],
      ["Ориентирование", "/groups/g2"],
    ]);
    const petrov = screen.getByRole("list", { name: "Группы: Петров Иван" });
    expect(within(petrov).getByRole("link", { name: "Ориентирование" })).toHaveAttribute("href", "/groups/g2");
    // The backend projection is the only source: no Groups list is fetched.
    expect(fetchMock.mock.calls.some(([input]) => /\/groups(\?|$)/.test(String(input)))).toBe(false);
  });

  it("shows no Group links for a child without current Groups", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/news", response: emptyEvents() },
      { match: "/events", response: emptyEvents() },
      { match: "/me/children", response: childrenResponse([child("c1", "Иванов", [])]) },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /Группы:/ })).not.toBeInTheDocument();
  });
});
