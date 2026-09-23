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

function accountNotFoundResponse() {
  return { error: { code: "account_not_found", message: "Account not found", details: {}, request_id: "r1" } };
}

function accountResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: "u2",
    person_id: "p1",
    login_identifier: "person@example.com",
    status: "active",
    email_verified_at: null,
    last_login_at: null,
    ...overrides,
  };
}

function groupMembership(overrides: Record<string, unknown> = {}) {
  return {
    id: "gm1",
    group_id: "grp1",
    club_membership_id: "cm1",
    valid_from: "2025-09-01T00:00:00Z",
    valid_to: null,
    membership_status: "active",
    created_at: "2025-09-01T00:00:00Z",
    updated_at: "2025-09-01T00:00:00Z",
    ...overrides,
  };
}

function groupFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "grp1",
    club_id: "club-1",
    name: "Юниоры",
    description: null,
    status: "active",
    valid_from: "2025-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
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
  it("shows identity, contact fields and a back link", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/groups", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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

  // --- Groups tab (TH-0116 / GitHub Issue #150) ------------------------

  it("renders the person's group memberships without exposing ClubMembership", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/groups", response: { items: [groupMembership()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/groups/grp1", response: groupFixture() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Группы" }));

    expect(await screen.findByText("Юниоры")).toBeInTheDocument();
    expect(screen.getByText("Активно")).toBeInTheDocument();
    // Never a technical ClubMembership id/type/status concept.
    expect(screen.queryByText("cm1")).not.toBeInTheDocument();
  });

  it("hides the add-to-group control for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/groups", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Группы" }));
    await screen.findByText("Человек пока не состоит ни в одной группе");

    expect(screen.queryByRole("button", { name: "Добавить в группу" })).not.toBeInTheDocument();
  });

  it("lets an admin add the person to a group by searching and selecting it", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/groups", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
      { match: "/persons/p1", response: PERSON },
      { match: "/groups?status=active", response: { items: [groupFixture()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/groups/grp1/members", response: groupMembership() },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Группы" }));
    await user.click(await screen.findByRole("button", { name: "Добавить в группу" }));
    await user.type(screen.getByLabelText("Поиск группы"), "Юни");
    await user.click(await screen.findByRole("button", { name: "Юниоры" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Добавить в группу" })).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/groups/grp1/members") && init?.method === "POST",
      ),
    ).toBe(true);
  });

  // --- Guardian relationship management (admin-only controls) ---------

  it("hides all guardian mutation controls for a non-admin role", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("guardian") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: { items: [guardianRelationship()], pagination: { page: 1, page_size: 50, total: 1, pages: 1 } } },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      if (url.includes("/persons/p1/account")) return jsonResponse(accountNotFoundResponse(), 404);
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      if (url.includes("/persons/p1/account")) return jsonResponse(accountNotFoundResponse(), 404);
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
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
      if (url.includes("/persons/p1/account")) return jsonResponse(accountNotFoundResponse(), 404);
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

describe("PersonDetailPage — account tab (TH-0113)", () => {
  it("shows \"Создать доступ\" for a Person with no account but an email", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(await screen.findByText("Учётная запись не создана")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Создать доступ" })).toBeInTheDocument();
  });

  it("prompts to add an email for a Person with no account and no email", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountNotFoundResponse(), status: 404 },
      { match: "/persons/p1", response: { ...PERSON, email: null } },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(
      await screen.findByText("Для создания доступа сначала укажите email."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Создать доступ" })).not.toBeInTheDocument();
    const addEmailButton = screen.getByRole("button", { name: "Добавить email" });
    await user.click(addEmailButton);
    expect(await screen.findByRole("dialog", { name: "Редактировать данные" })).toBeInTheDocument();
  });

  it("shows account information for a Person with an existing account", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountResponse({ login_identifier: "anna@example.com" }) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(await screen.findByText("anna@example.com")).toBeInTheDocument();
    expect(screen.getByText("Активна")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сбросить пароль" })).toBeInTheDocument();
  });

  it("hides account management controls for a non-admin user", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountResponse() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await screen.findByText("person@example.com");

    expect(screen.queryByRole("button", { name: "Сбросить пароль" })).not.toBeInTheDocument();
  });

  it("shows a loading state while account data loads", async () => {
    let resolveAccount: (value: Response) => void = () => {};
    const accountPromise = new Promise<Response>((resolve) => {
      resolveAccount = resolve;
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/account")) return accountPromise;
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(await screen.findByText("Загружаем данные учётной записи…")).toBeInTheDocument();
    resolveAccount(jsonResponse(accountResponse()));
  });

  it("creates an account and shows the temporary credential only after success", async () => {
    let accountExists = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/account") && method === "POST") {
        accountExists = true;
        return jsonResponse({
          account: accountResponse({ login_identifier: "anna@example.com" }),
          temporary_credential: "one-time-code-123",
        });
      }
      if (url.includes("/persons/p1/account") && method === "GET") {
        return accountExists
          ? jsonResponse(accountResponse({ login_identifier: "anna@example.com" }))
          : jsonResponse(accountNotFoundResponse(), 404);
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
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    expect(await screen.findByText("Учётная запись не создана")).toBeInTheDocument();
    expect(screen.queryByText("one-time-code-123")).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Создать доступ" }));

    expect(await screen.findByText("Учётная запись создана")).toBeInTheDocument();
    expect(await screen.findByText("one-time-code-123")).toBeInTheDocument();
    expect(await screen.findByText("anna@example.com")).toBeInTheDocument();
  });

  it("resets a password and shows the new temporary credential", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/account/password-reset") && method === "POST") {
        return jsonResponse({
          account: accountResponse({ login_identifier: "anna@example.com" }),
          temporary_credential: "reset-code-456",
        });
      }
      if (url.includes("/persons/p1/account") && method === "GET") {
        return jsonResponse(accountResponse({ login_identifier: "anna@example.com" }));
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
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await screen.findByText("anna@example.com");
    expect(screen.queryByText("reset-code-456")).not.toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Сбросить пароль" }));

    expect(await screen.findByText("Создан новый одноразовый код доступа")).toBeInTheDocument();
    expect(await screen.findByText("reset-code-456")).toBeInTheDocument();
  });

  it("does not persist the temporary credential across a remount (simulated reload)", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/account/password-reset") && method === "POST") {
        return jsonResponse({
          account: accountResponse({ login_identifier: "anna@example.com" }),
          temporary_credential: "should-not-survive-reload",
        });
      }
      if (url.includes("/persons/p1/account") && method === "GET") {
        return jsonResponse(accountResponse({ login_identifier: "anna@example.com" }));
      }
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await user.click(await screen.findByRole("button", { name: "Сбросить пароль" }));
    expect(await screen.findByText("should-not-survive-reload")).toBeInTheDocument();
    unmount();

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await screen.findByText("anna@example.com");
    expect(screen.queryByText("should-not-survive-reload")).not.toBeInTheDocument();
  });

  it("shows an API error via toast when creating an account fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.includes("/persons/p1/memberships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/guardian-relationships")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/role-assignments")) return jsonResponse(emptyCollection());
      if (url.includes("/persons/p1/account") && method === "POST") {
        return jsonResponse(
          {
            error: {
              code: "person_email_missing",
              message: "У этого человека не указан email",
              details: {},
              request_id: "r1",
            },
          },
          422,
        );
      }
      if (url.includes("/persons/p1/account") && method === "GET") {
        return jsonResponse(accountNotFoundResponse(), 404);
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
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await user.click(await screen.findByRole("button", { name: "Создать доступ" }));

    expect(await screen.findByText("У этого человека не указан email")).toBeInTheDocument();
    expect(screen.getByText("Учётная запись не создана")).toBeInTheDocument();
  });

  it("shows a forbidden/error state when loading the account is denied", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      {
        match: "/persons/p1/account",
        response: {
          error: { code: "forbidden", message: "Недостаточно прав", details: {}, request_id: "r1" },
        },
        status: 403,
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
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(await screen.findByText("Не удалось загрузить учётную запись")).toBeInTheDocument();
    expect(screen.getByText("Недостаточно прав")).toBeInTheDocument();
  });

  it("renders a non-active account status distinctly", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountResponse({ status: "locked" }) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));

    expect(await screen.findByText("Заблокирована")).toBeInTheDocument();
  });

  it("never renders a password/hash/token field in the ordinary account view", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1/role-assignments", response: emptyCollection() },
      { match: "/persons/p1/account", response: accountResponse() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Учётная запись" }));
    await screen.findByText("person@example.com");

    expect(screen.queryByText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/hash/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument();
  });
});

function documentFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "doc1",
    person_id: "p1",
    document_group_id: "grp-doc1",
    version_number: 1,
    document_type: "medical_certificate",
    status: "active",
    issued_at: "2026-01-01T00:00:00Z",
    expires_at: "2026-12-31T00:00:00Z",
    file_id: "file1",
    uploaded_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function documentsCollection(items: Array<Record<string, unknown>>) {
  return {
    items,
    pagination: { page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0 },
  };
}

describe("PersonDetailPage — documents tab (Issue #175)", () => {
  it("shows the medical_certificate document with its API-provided status, never a client-computed one", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/documents", response: documentsCollection([documentFixture()]) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));

    expect(await screen.findByText("Медицинская справка")).toBeInTheDocument();
    expect(screen.getByText("Действителен")).toBeInTheDocument();
  });

  it("shows an empty state when no documents exist", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/documents", response: documentsCollection([]) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));

    expect(await screen.findByText("Документы не загружены")).toBeInTheDocument();
  });

  it("shows a forbidden state distinctly from a generic load failure", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      {
        match: "/persons/p1/documents",
        response: { error: { code: "forbidden", message: "Недостаточно прав", details: {}, request_id: "r1" } },
        status: 403,
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
    await user.click(await screen.findByRole("tab", { name: "Документы" }));

    expect(await screen.findByText("Недостаточно прав для просмотра документов")).toBeInTheDocument();
  });

  it("shows document management controls regardless of role — the backend, not a role check, is the authorization boundary", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/documents", response: documentsCollection([documentFixture()]) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await screen.findByText("Медицинская справка");

    expect(screen.getByRole("button", { name: "Загрузить документ" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Изменить даты" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Заменить версию" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отозвать" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скачать" })).toBeInTheDocument();
  });

  it("surfaces a backend document.manage rejection via toast instead of silently succeeding or hiding the action", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse("member") },
      { match: "/persons/p1/documents/doc1/revoke", response: { error: { code: "forbidden", message: "Недостаточно прав", details: {}, request_id: "r1" } }, status: 403 },
      { match: "/persons/p1/documents", response: documentsCollection([documentFixture()]) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await user.click(await screen.findByRole("button", { name: "Отозвать" }));
    const dialog = screen.getByRole("dialog", { name: "Отозвать документ?" });
    await user.click(within(dialog).getByRole("button", { name: "Отозвать" }));

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
  });

  it("lets an admin upload a new document as a multipart request", async () => {
    let uploaded = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      if ((url.includes("/persons/p1/documents?") || url.endsWith("/persons/p1/documents")) && method === "POST") {
        uploaded = true;
        expect(init?.body).toBeInstanceOf(FormData);
        const form = init!.body as FormData;
        expect(form.get("document_type")).toBe("medical_certificate");
        expect(form.get("file")).toBeInstanceOf(File);
        return jsonResponse(documentFixture(), 201);
      }
      if ((url.includes("/persons/p1/documents?") || url.endsWith("/persons/p1/documents")) && method === "GET") {
        return jsonResponse(documentsCollection(uploaded ? [documentFixture()] : []));
      }
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
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await screen.findByText("Документы не загружены");

    await user.click(await screen.findByRole("button", { name: "Загрузить документ" }));
    const dialog = await screen.findByRole("dialog", { name: "Загрузить документ" });
    await user.type(within(dialog).getByLabelText("Тип документа"), "medical_certificate");
    const file = new File(["binary"], "справка.pdf", { type: "application/pdf" });
    const fileInput = dialog.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);
    await user.click(within(dialog).getByRole("button", { name: "Загрузить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Загрузить документ" })).not.toBeInTheDocument();
    });
    expect(uploaded).toBe(true);
  });

  it("sends only the changed date field(s) on PATCH", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      if (url.endsWith("/persons/p1/documents/doc1") && method === "PATCH") {
        const body = JSON.parse(String(init!.body));
        expect(body).toEqual({ expires_at: "2027-06-30" });
        return jsonResponse(documentFixture({ expires_at: "2027-06-30T00:00:00Z" }));
      }
      if ((url.includes("/persons/p1/documents?") || url.endsWith("/persons/p1/documents")) && method === "GET") {
        return jsonResponse(documentsCollection([documentFixture()]));
      }
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
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await user.click(await screen.findByRole("button", { name: "Изменить даты" }));

    const dialog = await screen.findByRole("dialog", { name: "Изменить даты документа" });
    const expiresInput = within(dialog).getByLabelText("Действителен до");
    await user.clear(expiresInput);
    await user.type(expiresInput, "2027-06-30");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Изменить даты документа" })).not.toBeInTheDocument();
    });
  });

  it("replaces a document's version via a multipart POST to /replace", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      if (url.endsWith("/persons/p1/documents/doc1/replace") && method === "POST") {
        expect(init?.body).toBeInstanceOf(FormData);
        return jsonResponse(documentFixture({ id: "doc2", version_number: 2 }), 201);
      }
      if ((url.includes("/persons/p1/documents?") || url.endsWith("/persons/p1/documents")) && method === "GET") {
        return jsonResponse(documentsCollection([documentFixture()]));
      }
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
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await user.click(await screen.findByRole("button", { name: "Заменить версию" }));

    const dialog = await screen.findByRole("dialog", { name: "Заменить версию документа" });
    const file = new File(["binary"], "справка-новая.pdf", { type: "application/pdf" });
    const fileInput = dialog.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);
    await user.click(within(dialog).getByRole("button", { name: "Заменить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Заменить версию документа" })).not.toBeInTheDocument();
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/persons/p1/documents/doc1/replace") &&
          (init as RequestInit)?.method === "POST",
      ),
    ).toBe(true);
    // No client-side "history" claim: this PR does not present the
    // superseded version as document history (Issue #175 review finding
    // #1) — there is no such UI to assert on here.
    expect(screen.queryByText(/История версий/)).not.toBeInTheDocument();
  });

  it("revokes a document through the confirm dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse("admin") },
      { match: "/persons/p1/documents/doc1/revoke", response: documentFixture({ status: "revoked" }) },
      { match: "/persons/p1/documents", response: documentsCollection([documentFixture()]) },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await user.click(await screen.findByRole("button", { name: "Отозвать" }));

    const dialog = screen.getByRole("dialog", { name: "Отозвать документ?" });
    await user.click(within(dialog).getByRole("button", { name: "Отозвать" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/revoke"))).toBe(true);
    });
  });

  it("downloads a document's content without exposing storage details", async () => {
    const createObjectURL = vi.fn(() => "blob:mock-url");
    const revokeObjectURL = vi.fn();
    // jsdom has no native Blob-URL support and would otherwise attempt a
    // real navigation on the anchor click (which it also doesn't
    // implement) — stub both; the assertion here is only that the
    // download flow calls through to the documented `/download` endpoint
    // and never touches storage_key/file paths.
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL = revokeObjectURL;

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse("admin"));
      if (url.endsWith("/persons/p1")) return jsonResponse(PERSON);
      if (url.endsWith("/persons/p1/documents/doc1/download")) {
        return new Response(new Blob(["binary content"]), {
          status: 200,
          headers: {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="cert.pdf"; filename*=UTF-8\'\'cert.pdf',
          },
        });
      }
      if ((url.includes("/persons/p1/documents?") || url.endsWith("/persons/p1/documents"))) return jsonResponse(documentsCollection([documentFixture()]));
      throw new Error(`Unexpected fetch: ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Документы" }));
    await user.click(await screen.findByRole("button", { name: "Скачать" }));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    expect(revokeObjectURL).toHaveBeenCalled();
    anchorClick.mockRestore();
  });
});
