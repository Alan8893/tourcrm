import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { PersonDetailPage } from "./PersonDetailPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

const PERSON = {
  id: "p1",
  first_name: "Анна",
  last_name: "Иванова",
  middle_name: "Сергеевна",
  birth_date: "2012-05-01",
  phone: "+79990001122",
  email: "anna@example.com",
  address: "г. Москва",
  photo_file_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const GUARDIAN_PERSON = {
  id: "g1",
  first_name: "Ольга",
  last_name: "Иванова",
  middle_name: null,
  birth_date: null,
  phone: null,
  email: null,
  address: null,
  photo_file_id: null,
  created_at: "2020-01-01T00:00:00Z",
  updated_at: "2020-01-01T00:00:00Z",
};

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

function emptyCollection() {
  return { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } };
}

function membership(overrides: Record<string, unknown> = {}) {
  return {
    id: "m1",
    club_id: "club-1",
    person_id: "p1",
    membership_type: "student",
    status: "active",
    joined_at: "2025-09-01T00:00:00Z",
    left_at: null,
    created_at: "2025-09-01T00:00:00Z",
    updated_at: "2025-09-01T00:00:00Z",
    ...overrides,
  };
}

function guardianRelationship(overrides: Record<string, unknown> = {}) {
  return {
    id: "gr1",
    guardian_person_id: "g1",
    child_person_id: "p1",
    relationship_type: "parent",
    status: "active",
    valid_from: "2020-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2020-01-01T00:00:00Z",
    updated_at: "2020-01-01T00:00:00Z",
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PersonDetailPage", () => {
  it("shows identity, contact fields and a back link, and renders membership periods", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: { items: [membership()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    expect(await screen.findByRole("heading", { name: "Иванова Анна Сергеевна" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Все люди/ })).toHaveAttribute("href", "/people");
    expect(screen.getByText("+79990001122")).toBeInTheDocument();
    expect(screen.getByText("anna@example.com")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Членство" }));

    expect(await screen.findByText("student")).toBeInTheDocument();
    expect(screen.getByText("Активно")).toBeInTheDocument();
  });

  it("resolves and renders guardian relationships without any primary-contact control", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      {
        match: "/persons/p1/guardian-relationships",
        response: { items: [guardianRelationship()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } },
      },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));

    expect(await screen.findByText("Иванова Ольга")).toBeInTheDocument();
    expect(screen.getByText(/parent/)).toBeInTheDocument();
    expect(screen.queryByText(/Основной контакт/)).not.toBeInTheDocument();
    expect(screen.queryByText(/[Пп]ервичный/)).not.toBeInTheDocument();
  });

  it("shows a not-found state for a person that does not exist or is not visible", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      {
        match: "/persons/missing",
        response: { error: { code: "not_found", message: "Person not found", details: {}, request_id: "r1" } },
        status: 404,
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/missing" },
    );

    expect(await screen.findByText("Человек не найден")).toBeInTheDocument();
  });

  it("shows a generic error state for a non-404 failure", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      {
        match: "/persons/p1",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
  });

  // --- Person edit ---------------------------------------------------

  it("offers birth_date editing to an admin but not to a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    expect(screen.getByLabelText("Дата рождения")).toBeInTheDocument();
  });

  it("does not offer birth_date editing to a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    expect(screen.getByRole("dialog", { name: "Редактировать данные" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Дата рождения")).not.toBeInTheDocument();
  });

  it("submits only the changed fields and closes on success", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    const phoneInput = screen.getByLabelText("Телефон");
    await user.clear(phoneInput);
    await user.type(phoneInput, "+70001112233");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Редактировать данные" })).not.toBeInTheDocument();
    });

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit | undefined][];
    const patchCall = calls.find(
      ([input, init]) => input.includes("/persons/p1") && init?.method === "PATCH",
    );
    expect(patchCall).toBeDefined();
    const body = JSON.parse(patchCall![1]!.body as string);
    expect(body).toEqual({ phone: "+70001112233" });
  });

  it("shows an API error via toast without closing the edit dialog", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));

    // Re-stub so the PATCH itself fails (overrides the earlier GET match).
    stubFetch([
      { match: "/persons/p1", response: { error: { code: "forbidden", message: "Недостаточно прав", details: {}, request_id: "r1" } }, status: 403 },
    ]);

    const phoneInput = screen.getByLabelText("Телефон");
    await user.clear(phoneInput);
    await user.type(phoneInput, "+70001112233");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Редактировать данные" })).toBeInTheDocument();
  });

  // --- Membership management (admin-only controls) --------------------

  it("hides all membership mutation controls for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: { items: [membership()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Членство" }));
    await screen.findByText("student");

    expect(screen.queryByRole("button", { name: "Добавить членство" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить тип" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить статус" })).not.toBeInTheDocument();
  });

  it("shows admin membership controls and limits status choices to the allowed transition graph", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: { items: [membership({ status: "active" })], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Членство" }));
    await screen.findByText("student");

    expect(screen.getByRole("button", { name: "Добавить членство" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Изменить статус" }));

    const select = screen.getByLabelText("Новый статус") as HTMLSelectElement;
    const options = within(select).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    // active -> suspended | inactive | archived (never "pending", never "active" itself).
    expect(options.sort()).toEqual(["archived", "inactive", "suspended"]);
  });

  it("hides the status action entirely for an archived (terminal) membership", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: { items: [membership({ status: "archived" })], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Членство" }));
    await screen.findByText("student");

    expect(screen.queryByRole("button", { name: "Изменить статус" })).not.toBeInTheDocument();
    // The type action remains available regardless of status.
    expect(screen.getByRole("button", { name: "Изменить тип" })).toBeInTheDocument();
  });

  it("creates a new membership as admin (rejoin flow) and closes the dialog on success", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
      { match: "/memberships", response: membership({ status: "active" }) },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Членство" }));
    await user.click(await screen.findByRole("button", { name: "Добавить членство" }));
    await user.type(screen.getByLabelText("Тип членства"), "student");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Новое членство" })).not.toBeInTheDocument();
    });
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/memberships"))).toBe(true);
  });

  // --- Guardian relationship management (admin-only controls) ---------

  it("hides all guardian mutation controls for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: { items: [guardianRelationship()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));
    await screen.findByText("Иванова Ольга");

    expect(screen.queryByRole("button", { name: "Добавить представителя" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить тип" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Прекратить" })).not.toBeInTheDocument();
  });

  it("lets an admin add a representative by searching and selecting a Person", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
      {
        match: "/persons?",
        response: { items: [GUARDIAN_PERSON], pagination: { page: 1, page_size: 20, total: 1, pages: 1 } },
      },
      { match: "/guardian-relationships", response: guardianRelationship() },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));
    await user.click(await screen.findByRole("button", { name: "Добавить представителя" }));
    await user.type(screen.getByLabelText("Поиск человека"), "Ольга");

    await user.click(await screen.findByRole("button", { name: "Иванова Ольга" }));
    await user.type(screen.getByLabelText("Тип связи"), "parent");
    await user.click(screen.getByRole("button", { name: "Добавить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Добавить представителя" })).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith("/guardian-relationships")),
    ).toBe(true);
  });

  it("hides the terminate action once a relationship is already revoked", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      {
        match: "/persons/p1/guardian-relationships",
        response: { items: [guardianRelationship({ status: "revoked" })], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } },
      },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));
    await screen.findByText("Иванова Ольга");

    expect(screen.queryByRole("button", { name: "Прекратить" })).not.toBeInTheDocument();
    // Editing the (historical) relationship type remains possible.
    expect(screen.getByRole("button", { name: "Изменить тип" })).toBeInTheDocument();
  });

  it("terminates a representative through the confirm dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      {
        match: "/persons/p1/guardian-relationships",
        response: { items: [guardianRelationship()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } },
      },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
      { match: "/terminate", response: guardianRelationship({ status: "revoked" }) },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));
    await user.click(await screen.findByRole("button", { name: "Прекратить" }));

    const dialog = screen.getByRole("dialog", { name: "Прекратить связь с представителем?" });
    await user.click(within(dialog).getByRole("button", { name: "Прекратить" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("/terminate")),
      ).toBe(true);
    });
  });
});

function roleAssignment(overrides: Record<string, unknown> = {}) {
  return {
    id: "ra1",
    person_id: "p1",
    role_code: "instructor",
    club_id: "club-1",
    valid_from: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("PersonDetailPage — roles tab (TH-0112)", () => {
  it("renders the person's current roles", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: { items: [roleAssignment({ role_code: "member" })], pagination: { page: 1, page_size: 1, total: 1, pages: 1 } },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));

    expect(await screen.findByText("Участник")).toBeInTheDocument();
  });

  it("shows an empty state when no roles are assigned", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));

    expect(await screen.findByText("Роли не назначены")).toBeInTheDocument();
  });

  it("renders multiple roles at once", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: {
          items: [roleAssignment({ id: "ra1", role_code: "instructor" }), roleAssignment({ id: "ra2", role_code: "guardian" })],
          pagination: { page: 1, page_size: 2, total: 2, pages: 1 },
        },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));

    expect(await screen.findByText("Инструктор")).toBeInTheDocument();
    expect(screen.getByText("Родитель")).toBeInTheDocument();
  });

  it("lets an admin add a role, and the list reflects the new role after the mutation", async () => {
    let roles: Array<Record<string, unknown>> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments") && method === "POST") {
        const created = roleAssignment({ id: "ra-new", role_code: "admin" });
        roles = [...roles, created];
        return jsonResponse(created, 201);
      }
      if (url.includes("/persons/p1/role-assignments") && method === "GET") {
        return jsonResponse({ items: roles, pagination: { page: 1, page_size: roles.length || 1, total: roles.length, pages: roles.length ? 1 : 0 } });
      }
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    expect(await screen.findByText("Роли не назначены")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Добавить роль" }));
    await user.click(await screen.findByRole("button", { name: "Добавить" }));

    expect(await screen.findByText("Роль добавлена")).toBeInTheDocument();
    expect(await screen.findByText("Администратор")).toBeInTheDocument();
    expect(screen.queryByText("Роли не назначены")).not.toBeInTheDocument();
  });

  it("does not offer an already-assigned role again when adding", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: {
          items: [roleAssignment({ id: "ra1", role_code: "instructor" }), roleAssignment({ id: "ra2", role_code: "guardian" })],
          pagination: { page: 1, page_size: 2, total: 2, pages: 1 },
        },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    await user.click(await screen.findByRole("button", { name: "Добавить роль" }));

    const dialog = await screen.findByRole("dialog", { name: "Добавить роль" });
    const select = within(dialog).getByLabelText("Роль") as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((option) => option.textContent);
    expect(optionLabels).toEqual(["Администратор", "Участник"]);
  });

  it("lets an admin remove a role, and the list reflects removal after the mutation", async () => {
    let roles: Array<Record<string, unknown>> = [roleAssignment({ id: "ra1", role_code: "guardian" })];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments/guardian") && method === "DELETE") {
        roles = [];
        return new Response(null, { status: 204 });
      }
      if (url.includes("/persons/p1/role-assignments") && method === "GET") {
        return jsonResponse({ items: roles, pagination: { page: 1, page_size: roles.length || 1, total: roles.length, pages: roles.length ? 1 : 0 } });
      }
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    expect(await screen.findByText("Родитель")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Удалить" }));

    expect(await screen.findByText("Роль удалена")).toBeInTheDocument();
    expect(await screen.findByText("Роли не назначены")).toBeInTheDocument();
  });

  it("shows the guardian role's \"Привязать ребёнка\" action only when the guardian role is active", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: { items: [roleAssignment({ role_code: "guardian" })], pagination: { page: 1, page_size: 1, total: 1, pages: 1 } },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));

    expect(await screen.findByRole("button", { name: "Привязать ребёнка" })).toBeInTheDocument();
  });

  it("does not show \"Привязать ребёнка\" without the guardian role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: { items: [roleAssignment({ role_code: "member" })], pagination: { page: 1, page_size: 1, total: 1, pages: 1 } },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    await screen.findByText("Участник");

    expect(screen.queryByRole("button", { name: "Привязать ребёнка" })).not.toBeInTheDocument();
  });

  it("hides role-management controls for a non-admin (unauthorized) user", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1/role-assignments",
        response: { items: [roleAssignment({ role_code: "guardian" })], pagination: { page: 1, page_size: 1, total: 1, pages: 1 } },
      },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    await screen.findByText("Родитель");

    expect(screen.queryByRole("button", { name: "Добавить роль" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Привязать ребёнка" })).not.toBeInTheDocument();
  });

  it("shows a mutation error via toast when adding a role fails, without changing the list", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments") && method === "POST") {
        return jsonResponse(
          {
            error: {
              code: "person_has_no_user_account",
              message: "У этого человека ещё нет учётной записи",
              details: {},
              request_id: "r1",
            },
          },
          422,
        );
      }
      if (url.includes("/persons/p1/role-assignments") && method === "GET") {
        return jsonResponse(emptyCollection());
      }
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Роли" }));
    await user.click(await screen.findByRole("button", { name: "Добавить роль" }));
    await user.click(await screen.findByRole("button", { name: "Добавить" }));

    expect(await screen.findByText("У этого человека ещё нет учётной записи")).toBeInTheDocument();
    expect(screen.getByText("Роли не назначены")).toBeInTheDocument();
  });
});
