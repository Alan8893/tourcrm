import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { AchievementsPage } from "./AchievementsPage";
import { AchievementDefinitionPage } from "./AchievementDefinitionPage";
import { NormativeSetPage } from "./NormativeSetPage";
import { AdministratorGuard } from "../shell/AdministratorGuard";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi, requests, type MockApiHandler } from "../test/mockApi";
import type {
  AchievementAward,
  AchievementDefinition,
  NormativeVersion,
  RuleCatalog,
  RuleVersion,
} from "../api/achievements";

/**
 * Issue #220 — Achievements administration UI. The backend decides every
 * rule; these tests assert the UI sends exactly the Administrator's input,
 * renders what the backend returns, and shows the backend's refusals —
 * never that it evaluates or pre-filters anything itself.
 */

function meResponse(roleCodes: string[]) {
  return {
    user: {
      id: "u1",
      login_identifier: "admin@example.com",
      status: "active",
      email_verified_at: null,
      person: {
        id: "p0",
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

function collection<T>(items: T[]) {
  return { items, pagination: { page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0 } };
}

const catalog: RuleCatalog = {
  metrics: [{ code: "completed_trips", label: "Завершённые походы", description: "" }],
  logic_operators: ["AND", "OR"],
  comparison_operators: [">=", ">", "==", "<=", "<"],
  max_depth: 8,
};

function definition(overrides: Partial<AchievementDefinition> = {}): AchievementDefinition {
  return {
    id: "d1",
    code: "first-trip",
    name: "Первый поход",
    description: null,
    source: "club",
    award_method: "both",
    repeatability: "non_repeatable",
    status: "active",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

function award(overrides: Partial<AchievementAward> = {}): AchievementAward {
  return {
    id: "a1",
    definition_id: "d1",
    definition_code: "first-trip",
    definition_name: "Первый поход",
    definition_source: "club",
    definition_repeatability: "non_repeatable",
    person_id: "p1",
    person_name: "Петров Иван",
    award_method: "automatic",
    rule_version_id: "r1",
    rule_version_number: 1,
    normative_set_version_id: null,
    normative_version_number: null,
    awarded_at: "2026-10-02T10:00:00Z",
    awarded_by_user_id: null,
    verification_note: null,
    evaluation_trigger: "event",
    evaluated_metrics: { completed_trips: 1 },
    status: "active",
    revoked_at: null,
    revoked_by_user_id: null,
    revocation_reason: null,
    ...overrides,
  };
}

function ruleVersion(overrides: Partial<RuleVersion> = {}): RuleVersion {
  return {
    id: "r1",
    definition_id: "d1",
    version_number: 1,
    condition: { logic: "AND", conditions: [{ metric: "completed_trips", operator: ">=", value: 1 }] },
    normative_set_version_id: null,
    status: "active",
    is_used: true,
    created_by_user_id: "u1",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides,
  };
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, fragment: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => String(input).includes(fragment) && (init?.method ?? "GET").toUpperCase() === method,
  );
  return call ? JSON.parse(String(call[1]?.body ?? "{}")) : undefined;
}

function renderAchievements(route: string, handlers: MockApiHandler[]) {
  const fetchMock = mockApi(handlers);
  renderWithProviders(
    <Routes>
      <Route path="/achievements" element={<AchievementsPage />} />
      <Route element={<AdministratorGuard />}>
        <Route path="/achievements/definitions/:definitionId" element={<AchievementDefinitionPage />} />
        <Route path="/achievements/normative-sets/:setId" element={<NormativeSetPage />} />
      </Route>
    </Routes>,
    { route },
  );
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Achievements — roles", () => {
  it.each([["instructor"], ["member"], ["guardian"]])(
    "%s keeps the placeholder and never calls the administration API",
    async (role) => {
      const fetchMock = renderAchievements("/achievements", [
        { match: "/auth/me", body: meResponse([role]) },
      ]);
      expect(await screen.findByText(/Раздел появится/)).toBeInTheDocument();
      expect(requests(fetchMock).some(([, url]) => url.includes("/achievements"))).toBe(false);
    },
  );

  it("non-administrators get the 403 state on a definition page", async () => {
    renderAchievements("/achievements/definitions/d1", [
      { match: "/auth/me", body: meResponse(["instructor"]) },
    ]);
    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
  });
});

describe("Achievements — definitions", () => {
  it("lists definitions as returned by the backend", async () => {
    renderAchievements("/achievements", [
      { match: "/auth/me", body: meResponse(["admin"]) },
      { match: "/achievements/definitions", body: collection([definition(), definition({ id: "d2", name: "Турист", status: "inactive", source: "fstr" })]) },
    ]);
    const cards = await screen.findAllByTestId("definition-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[1]).getByText("ФСТР")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Неактивно")).toBeInTheDocument();
  });

  it("creates a definition with exactly the entered values", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements("/achievements", [
      { match: "/auth/me", body: meResponse(["admin"]) },
      { method: "POST", match: "/achievements/definitions", status: 201, body: definition({ status: "inactive" }) },
      { match: "/achievements/definitions", body: collection([]) },
    ]);
    await user.click(await screen.findByRole("button", { name: /Создать достижение/ }));
    await user.type(screen.getByLabelText("Код"), "first-trip");
    await user.type(screen.getByLabelText("Название"), "Первый поход");
    await user.selectOptions(screen.getByLabelText("Источник"), "fstr");
    await user.selectOptions(screen.getByLabelText("Способ выдачи"), "manual");
    await user.selectOptions(screen.getByLabelText("Повторяемость"), "repeatable");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/achievements/definitions")).toEqual({
        code: "first-trip",
        name: "Первый поход",
        description: null,
        source: "fstr",
        award_method: "manual",
        repeatability: "repeatable",
      }),
    );
  });

  it("runs reconciliation on demand and reports the backend's result", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements("/achievements", [
      { match: "/auth/me", body: meResponse(["admin"]) },
      { method: "POST", match: "/achievements/reconciliation", body: { evaluated_people: 3, evaluated_rules: 1, awards_created: 2 } },
      { match: "/achievements/definitions", body: collection([]) },
    ]);
    await user.click(await screen.findByRole("button", { name: "Запустить сверку" }));
    expect(await screen.findByText(/выдано недостающих достижений — 2/)).toBeInTheDocument();
    expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/achievements/reconciliation"]);
  });
});

describe("Achievements — awards", () => {
  const handlers: MockApiHandler[] = [
    { match: "/auth/me", body: meResponse(["admin"]) },
    { match: "/achievements/definitions", body: collection([definition()]) },
  ];

  it("shows automatic and manual provenance distinctly and the revoked state", async () => {
    const user = userEvent.setup();
    renderAchievements("/achievements", [
      ...handlers,
      {
        match: "/achievements/awards",
        body: collection([
          award(),
          award({
            id: "a2",
            award_method: "manual",
            evaluation_trigger: null,
            evaluated_metrics: null,
            awarded_by_user_id: "u1",
            verification_note: "Маршрутная книжка",
            status: "revoked",
            revoked_at: "2026-10-03T10:00:00Z",
            revocation_reason: "Ошибка выдачи",
          }),
        ]),
      },
    ]);
    await user.click(await screen.findByRole("tab", { name: "Выдачи" }));
    const cards = await screen.findAllByTestId("award-card");
    expect(within(cards[0]).getByText("Автоматически (движок), по событию")).toBeInTheDocument();
    expect(within(cards[0]).getByRole("button", { name: "Отозвать" })).toBeInTheDocument();
    expect(within(cards[1]).getByText("Вручную (проверено администратором)")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Маршрутная книжка")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Ошибка выдачи")).toBeInTheDocument();
    expect(within(cards[1]).queryByRole("button", { name: "Отозвать" })).not.toBeInTheDocument();
  });

  it("revokes with the entered reason", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements("/achievements", [
      ...handlers,
      { method: "POST", match: "/achievements/awards/a1/revoke", body: award({ status: "revoked" }) },
      { match: "/achievements/awards", body: collection([award()]) },
    ]);
    await user.click(await screen.findByRole("tab", { name: "Выдачи" }));
    await user.click(await screen.findByRole("button", { name: "Отозвать" }));
    const dialog = await screen.findByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Отозвать" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Причина отзыва"), "Ошибка выдачи");
    await user.click(submit);
    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/awards/a1/revoke")).toEqual({ reason: "Ошибка выдачи" }),
    );
  });

  it("issues a manual award and shows the backend's refusal", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements("/achievements", [
      ...handlers,
      {
        method: "POST",
        match: "/achievements/awards",
        status: 422,
        body: { error: { code: "recipient_not_member", message: "x", details: {}, request_id: "r" } },
      },
      { match: "/achievements/awards", body: collection([]) },
      {
        match: "/persons",
        body: collection([{ id: "p9", first_name: "Олег", last_name: "Сидоров", middle_name: null }]),
      },
    ]);
    await user.click(await screen.findByRole("tab", { name: "Выдачи" }));
    await user.click(await screen.findByRole("button", { name: /Выдать вручную/ }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByRole("option", { name: "Первый поход" })).toBeInTheDocument(),
    );
    await user.selectOptions(within(dialog).getByLabelText("Достижение"), "d1");
    await user.type(within(dialog).getByLabelText("Поиск участника"), "Сид");
    await waitFor(() =>
      expect(within(dialog).getByRole("option", { name: "Сидоров Олег" })).toBeInTheDocument(),
    );
    await user.selectOptions(within(dialog).getByLabelText("Участник"), "p9");
    await user.type(within(dialog).getByLabelText(/Основание/), "Проверено");
    await user.click(within(dialog).getByRole("button", { name: "Выдать" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Достижение может получить только участник",
    );
    expect(bodyOf(fetchMock, "POST", "/achievements/awards")).toEqual({
      definition_id: "d1",
      person_id: "p9",
      verification_note: "Проверено",
    });
  });
});

describe("Achievement definition page", () => {
  function pageHandlers(extra: MockApiHandler[] = []): MockApiHandler[] {
    return [
      { match: "/auth/me", body: meResponse(["admin"]) },
      ...extra,
      { match: "/achievements/rule-catalog", body: catalog },
      { match: "/achievements/definitions/d1/rule-versions", body: collection([ruleVersion()]) },
      { match: "/achievements/definitions/d1", body: definition() },
      { match: "/achievements/normative-sets", body: collection([]) },
      { match: "/achievements/awards", body: collection([]) },
    ];
  }

  it("shows rule versions; a used version offers no edit", async () => {
    renderAchievements("/achievements/definitions/d1", pageHandlers());
    const card = await screen.findByTestId("rule-version-card");
    expect(await within(card).findByText("Завершённые походы >= 1")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
  });

  it("creates a nested rule version from the backend catalog", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements(
      "/achievements/definitions/d1",
      pageHandlers([
        {
          method: "POST",
          match: "/achievements/definitions/d1/rule-versions",
          status: 201,
          body: ruleVersion({ id: "r2", version_number: 2, status: "inactive", is_used: false }),
        },
      ]),
    );
    await screen.findByTestId("rule-version-card");
    await user.click(screen.getByRole("button", { name: /Новая версия правила/ }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Логика группы"), "OR");
    await user.click(within(dialog).getByRole("button", { name: "Группа" }));
    const value = within(dialog).getAllByLabelText("Значение")[0];
    await user.clear(value);
    await user.type(value, "3");
    await user.click(within(dialog).getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/definitions/d1/rule-versions")).toEqual({
        condition: {
          logic: "OR",
          conditions: [
            { metric: "completed_trips", operator: ">=", value: 3 },
            { logic: "AND", conditions: [{ metric: "completed_trips", operator: ">=", value: 1 }] },
          ],
        },
        normative_set_version_id: null,
      }),
    );
  });

  it("deactivates the definition through the backend", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements(
      "/achievements/definitions/d1",
      pageHandlers([
        { method: "POST", match: "/achievements/definitions/d1/deactivate", body: definition({ status: "inactive" }) },
      ]),
    );
    await user.click(await screen.findByRole("button", { name: "Деактивировать" }));
    await waitFor(() =>
      expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/achievements/definitions/d1/deactivate"]),
    );
  });
});

describe("Normative set page", () => {
  const version: NormativeVersion = {
    id: "nv1",
    normative_set_id: "s1",
    version_number: 1,
    source_organization: "ФСТР",
    document_title: "Знаки отличия",
    source_url: "https://tssr.ru/child/",
    document_version: "2025",
    publication_date: null,
    effective_from: "2025-02-01",
    effective_to: null,
    status: "inactive",
    is_used: false,
    created_by_user_id: "u1",
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
  };

  it("shows version source metadata and activates a version", async () => {
    const user = userEvent.setup();
    const fetchMock = renderAchievements("/achievements/normative-sets/s1", [
      { match: "/auth/me", body: meResponse(["admin"]) },
      { method: "POST", match: "/normative-versions/nv1/activate", body: { ...version, status: "active" } },
      { match: "/achievements/normative-sets/s1/versions", body: collection([version]) },
      { match: "/achievements/normative-sets/s1", body: { id: "s1", code: "fstr", name: "ФСТР", description: null, created_at: "", updated_at: "" } },
    ]);
    const card = await screen.findByTestId("normative-version-card");
    expect(within(card).getByText("https://tssr.ru/child/")).toBeInTheDocument();
    expect(within(card).getByText("01.02.2025")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Активировать" }));
    await waitFor(() =>
      expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/achievements/normative-versions/nv1/activate"]),
    );
  });

  it("offers no edit for a used version", async () => {
    renderAchievements("/achievements/normative-sets/s1", [
      { match: "/auth/me", body: meResponse(["admin"]) },
      { match: "/achievements/normative-sets/s1/versions", body: collection([{ ...version, is_used: true }]) },
      { match: "/achievements/normative-sets/s1", body: { id: "s1", code: "fstr", name: "ФСТР", description: null, created_at: "", updated_at: "" } },
    ]);
    const card = await screen.findByTestId("normative-version-card");
    expect(within(card).queryByRole("button", { name: "Изменить" })).not.toBeInTheDocument();
  });
});
