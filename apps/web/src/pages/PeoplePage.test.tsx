import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { PeoplePage } from "./PeoplePage";
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

function peopleResponse(
  items: Array<{
    id: string;
    first_name: string;
    last_name: string;
    birth_date: string | null;
    role_codes?: string[];
  }>,
  pagination: { page: number; page_size: number; total: number; pages: number },
) {
  return {
    items: items.map((item) => ({
      id: item.id,
      first_name: item.first_name,
      last_name: item.last_name,
      middle_name: null,
      birth_date: item.birth_date,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      role_codes: item.role_codes ?? [],
    })),
    pagination,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PeoplePage", () => {
  it("shows a loading state before the list resolves", () => {
    stubFetch([
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(screen.getByText("Загружаем людей…")).toBeInTheDocument();
  });

  it("renders a readable list of people on success", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [
            { id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: "2012-05-01" },
            { id: "p2", first_name: "Пётр", last_name: "Сидоров", birth_date: null },
          ],
          { page: 1, page_size: 20, total: 2, pages: 1 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByRole("link", { name: "Иванова Анна" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Сидоров Пётр" })).toBeInTheDocument();
    expect(screen.getByText(/Дата рождения/)).toBeInTheDocument();
  });

  // --- Role display (TH-0114 / ADR-0039) --------------------------------

  it("shows a human-readable label for a person's single active role", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [
            {
              id: "p1",
              first_name: "Анна",
              last_name: "Иванова",
              birth_date: null,
              role_codes: ["instructor"],
            },
          ],
          { page: 1, page_size: 20, total: 1, pages: 1 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByRole("link", { name: "Иванова Анна" })).toBeInTheDocument();
    expect(screen.getByText("Инструктор")).toBeInTheDocument();
  });

  it("shows every active role when a person has more than one", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [
            {
              id: "p1",
              first_name: "Анна",
              last_name: "Иванова",
              birth_date: null,
              role_codes: ["admin", "guardian"],
            },
          ],
          { page: 1, page_size: 20, total: 1, pages: 1 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);

    await screen.findByRole("link", { name: "Иванова Анна" });
    expect(screen.getByText("Администратор")).toBeInTheDocument();
    expect(screen.getByText("Родитель")).toBeInTheDocument();
  });

  it("renders a person with no active roles without any role label or crash", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null }],
          { page: 1, page_size: 20, total: 1, pages: 1 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByRole("link", { name: "Иванова Анна" })).toBeInTheDocument();
    expect(screen.queryByText("Администратор")).not.toBeInTheDocument();
    expect(screen.queryByText("Инструктор")).not.toBeInTheDocument();
    expect(screen.queryByText("Участник")).not.toBeInTheDocument();
    expect(screen.queryByText("Родитель")).not.toBeInTheDocument();
  });

  it("shows the empty-people state when there are no people at all", async () => {
    stubFetch([
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByText("Пока нет ни одного человека")).toBeInTheDocument();
  });

  it("shows the no-results state when a search matches nobody", async () => {
    const fetchMock = stubFetch([
      { match: "/persons?page=1&page_size=20&search=", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/persons?page=1", response: peopleResponse([{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null }], { page: 1, page_size: 20, total: 1, pages: 1 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByRole("link", { name: "Иванова Анна" });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Поиск по имени или роли"), "Зз");

    await waitFor(
      () => {
        expect(
          fetchMock.mock.calls.some(([input]) => String(input).includes("search=%D0%97%D0%B7")),
        ).toBe(true);
      },
      { timeout: 2000 },
    );
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("sends a role label typed into the single search field to the backend and renders the match (TH-0114)", async () => {
    const fetchMock = stubFetch([
      {
        match: "search=%D0%98%D0%BD%D1%81%D1%82%D1%80%D1%83%D0%BA%D1%82%D0%BE%D1%80",
        response: peopleResponse(
          [
            {
              id: "p1",
              first_name: "Пётр",
              last_name: "Кузнецов",
              birth_date: null,
              role_codes: ["instructor"],
            },
          ],
          { page: 1, page_size: 20, total: 1, pages: 1 },
        ),
      },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByText("Пока нет ни одного человека");

    const user = userEvent.setup();
    // Never a separate role dropdown/filter — the same one search field
    // used for names.
    await user.type(screen.getByLabelText("Поиск по имени или роли"), "Инструктор");

    expect(await screen.findByRole("link", { name: "Кузнецов Пётр" })).toBeInTheDocument();
    expect(screen.getByText("Инструктор")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(
          "search=%D0%98%D0%BD%D1%81%D1%82%D1%80%D1%83%D0%BA%D1%82%D0%BE%D1%80",
        ),
      ),
    ).toBe(true);
  });

  it("shows an error state when the request fails", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByText("Не удалось загрузить людей")).toBeInTheDocument();
  });

  it("moves to the next page and requests it from the server", async () => {
    const fetchMock = stubFetch([
      {
        match: "/persons?page=2",
        response: peopleResponse(
          [{ id: "p3", first_name: "Олег", last_name: "Петров", birth_date: null }],
          { page: 2, page_size: 20, total: 21, pages: 2 },
        ),
      },
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null }],
          { page: 1, page_size: 20, total: 21, pages: 2 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByRole("link", { name: "Иванова Анна" });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByRole("link", { name: "Петров Олег" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/persons?page=2"))).toBe(true);
  });

  // --- Person create (TH-0104) -----------------------------------------

  it("hides the create button for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByText("Пока нет ни одного человека");

    expect(screen.queryByRole("button", { name: "Добавить человека" })).not.toBeInTheDocument();
  });

  it("shows the create button for an admin and validates required fields on the first wizard step", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));

    expect(screen.getByRole("dialog", { name: /Новый человек/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();

    await user.type(screen.getByLabelText("Фамилия"), "Смирнова");
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.type(screen.getByLabelText("Имя"), "Мария");
    expect(screen.getByRole("button", { name: "Далее" })).toBeEnabled();
  });

  it("creates a Person via the wizard (admin role, no contextual step) and navigates to its detail page", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      {
        match: "/persons/wizard",
        response: {
          person: {
            id: "new-1",
            first_name: "Мария",
            last_name: "Смирнова",
            middle_name: null,
            birth_date: null,
            phone: null,
            email: null,
            address: null,
            photo_file_id: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            role_codes: ["admin"],
          },
          temporary_credential: null,
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<div>Person detail page</div>} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Смирнова");
    await user.type(screen.getByLabelText("Имя"), "Мария");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Администратор" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Person detail page")).toBeInTheDocument();
  });

  it("calls only POST /persons/wizard — never separate group/role requests — and refreshes the list", async () => {
    // TH-0116 / Issue #150: Person + account provisioning + initial role
    // is one atomic backend operation; the frontend must never orchestrate
    // it as several separate requests.
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      {
        match: "/persons/wizard",
        response: {
          person: {
            id: "new-1",
            first_name: "Мария",
            last_name: "Смирнова",
            middle_name: null,
            birth_date: null,
            phone: null,
            email: null,
            address: null,
            photo_file_id: null,
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            role_codes: ["admin"],
          },
          temporary_credential: null,
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<div>Person detail page</div>} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Смирнова");
    await user.type(screen.getByLabelText("Имя"), "Мария");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Администратор" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await screen.findByText("Person detail page");

    const wizardCalls = fetchMock.mock.calls.filter(
      ([input, init]) => String(input).endsWith("/persons/wizard") && init?.method === "POST",
    );
    expect(wizardCalls).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships"))).toBe(
      false,
    );
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/role-assignments")),
    ).toBe(false);
  });

  it("shows an API error via toast and keeps the wizard open", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      {
        match: "/persons/wizard",
        response: { error: { code: "validation_error", message: "Некорректные данные", details: {}, request_id: "r1" } },
        status: 422,
      },
    ]);

    renderWithProviders(<PeoplePage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Смирнова");
    await user.type(screen.getByLabelText("Имя"), "Мария");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Администратор" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Некорректные данные")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: /Новый человек/ })).toBeInTheDocument();
  });

  // --- Wizard: role-specific contextual setup (TH-0116 / Issue #150) -----

  it("allows an instructor to finish the wizard with zero groups selected", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/groups?status=active", response: { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } } },
      {
        match: "/persons/wizard",
        response: {
          person: { id: "new-2", first_name: "Пётр", last_name: "Кузнецов", middle_name: null, birth_date: null, phone: null, email: null, address: null, photo_file_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", role_codes: ["instructor"] },
          temporary_credential: null,
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<div>Person detail page</div>} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Кузнецов");
    await user.type(screen.getByLabelText("Имя"), "Пётр");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Инструктор" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByLabelText("Поиск группы")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Person detail page")).toBeInTheDocument();
  });

  it("blocks finishing the wizard for a member with no group selected", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/groups?status=active", response: { items: [{ id: "grp1", club_id: "club-1", name: "Юниоры", description: null, status: "active", valid_from: "2025-01-01T00:00:00Z", valid_to: null, created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z" }], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
    ]);

    renderWithProviders(<PeoplePage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Сидорова");
    await user.type(screen.getByLabelText("Имя"), "Ольга");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Участник" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));

    await screen.findByText("Юниоры");
    expect(screen.getByRole("button", { name: "Создать" })).toBeDisabled();

    await user.click(screen.getByLabelText("Юниоры"));
    expect(screen.getByRole("button", { name: "Создать" })).toBeEnabled();
  });

  it("blocks finishing the wizard for a guardian with no child selected", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/persons?page=1&search=", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Кузнецова");
    await user.type(screen.getByLabelText("Имя"), "Мария");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByRole("button", { name: "Родитель" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));

    // Section 11: the search field is pre-filled with the guardian's own
    // surname as a search convenience only.
    expect(await screen.findByLabelText("Поиск ребёнка")).toHaveValue("Кузнецова");
    expect(screen.getByRole("button", { name: "Создать" })).toBeDisabled();
  });

  // --- Wizard: switching role clears stale contextual selections ---------

  it("clears a selected group when switching from Member to Admin before finishing", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/groups?status=active", response: { items: [{ id: "grp1", club_id: "club-1", name: "Юниоры", description: null, status: "active", valid_from: "2025-01-01T00:00:00Z", valid_to: null, created_at: "2025-01-01T00:00:00Z", updated_at: "2025-01-01T00:00:00Z" }], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      {
        match: "/persons/wizard",
        response: {
          person: { id: "new-3", first_name: "Ольга", last_name: "Сидорова", middle_name: null, birth_date: null, phone: null, email: null, address: null, photo_file_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", role_codes: ["admin"] },
          temporary_credential: null,
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<div>Person detail page</div>} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Сидорова");
    await user.type(screen.getByLabelText("Имя"), "Ольга");
    await user.click(screen.getByRole("button", { name: "Далее" }));

    // 1. select Member, 2. select a group.
    await user.click(await screen.findByRole("button", { name: "Участник" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByLabelText("Юниоры"));

    // 3. go back, 4. switch to Admin, which has no contextual step at all.
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await user.click(await screen.findByRole("button", { name: "Администратор" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await screen.findByText("Person detail page");

    const wizardCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/persons/wizard"));
    expect(wizardCall).toBeDefined();
    const body = JSON.parse(String((wizardCall?.[1] as RequestInit).body));
    expect(body.role_code).toBe("admin");
    expect(body.group_ids).toEqual([]);
    expect(body.child_person_ids).toEqual([]);
  });

  it("clears a selected child when switching from Guardian to Instructor before finishing", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [{ id: "child-1", first_name: "Иван", last_name: "Волков", birth_date: null }],
          { page: 1, page_size: 20, total: 1, pages: 1 },
        ),
      },
      { match: "/groups?status=active", response: { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } } },
      {
        match: "/persons/wizard",
        response: {
          person: { id: "new-4", first_name: "Анна", last_name: "Волкова", middle_name: null, birth_date: null, phone: null, email: null, address: null, photo_file_id: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", role_codes: ["instructor"] },
          temporary_credential: null,
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<div>Person detail page</div>} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить человека" }));
    await user.type(screen.getByLabelText("Фамилия"), "Волкова");
    await user.type(screen.getByLabelText("Имя"), "Анна");
    await user.click(screen.getByRole("button", { name: "Далее" }));

    // 1. select Guardian, 2. select a child.
    await user.click(await screen.findByRole("button", { name: "Родитель" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await user.click(await screen.findByLabelText("Волков Иван"));

    // 3. go back, 4. switch to Instructor, finish with zero groups.
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await user.click(await screen.findByRole("button", { name: "Инструктор" }));
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await screen.findByLabelText("Поиск группы");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await screen.findByText("Person detail page");

    const wizardCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/persons/wizard"));
    expect(wizardCall).toBeDefined();
    const body = JSON.parse(String((wizardCall?.[1] as RequestInit).body));
    expect(body.role_code).toBe("instructor");
    expect(body.child_person_ids).toEqual([]);
  });
});
