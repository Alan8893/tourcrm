import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EventsPage } from "./EventsPage";
import styles from "./EventsPage.module.css";
import { renderWithProviders, renderWithHistory, stubFetch } from "../test/renderWithProviders";
import { formatDateParam, monthLabel, monthRange } from "../domain/calendarDate";

function meResponse(overrides: Partial<{ userId: string; roleCode: string }> = {}) {
  return {
    user: {
      id: overrides.userId ?? "u1",
      login_identifier: "user@example.com",
      status: "active",
      email_verified_at: null,
      person: { first_name: "Анна", last_name: "Иванова", middle_name: null, birth_date: null },
    },
    role_assignments: [{ role_code: overrides.roleCode ?? "admin", club_id: "club-1", scope_type: "all" }],
  };
}

function groupsResponse(items: Array<{ id: string; name: string }> = []) {
  return {
    items: items.map((item) => ({
      id: item.id,
      club_id: "club-1",
      name: item.name,
      description: null,
      status: "active",
      valid_from: "2020-01-01T00:00:00Z",
      valid_to: null,
      created_at: "2020-01-01T00:00:00Z",
      updated_at: "2020-01-01T00:00:00Z",
    })),
    pagination: { page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0 },
  };
}

type CalendarItemFixture = {
  id: string;
  kind?: "event" | "occurrence";
  event_type?: string;
  title?: string;
  start_at: string;
  end_at: string;
  status?: string;
  cancellation_reason?: string | null;
  series_id?: string | null;
  series_version?: number | null;
};

function calendarItem(fixture: CalendarItemFixture) {
  return {
    id: fixture.id,
    kind: fixture.kind ?? "event",
    club_id: "club-1",
    event_type: fixture.event_type ?? "lesson",
    title: fixture.title ?? "Событие",
    description: null,
    start_at: fixture.start_at,
    end_at: fixture.end_at,
    timezone: "Europe/Moscow",
    status: fixture.status ?? "published",
    cancellation_reason: fixture.cancellation_reason ?? null,
    series_id: fixture.series_id ?? null,
    series_version: fixture.series_version ?? null,
  };
}

function calendarResponse(
  items: CalendarItemFixture[],
  pagination: { page?: number; pages?: number; total?: number } = {},
) {
  const page = pagination.page ?? 1;
  const pages = pagination.pages ?? 1;
  return {
    items: items.map(calendarItem),
    pagination: { page, page_size: 100, total: pagination.total ?? items.length, pages },
  };
}

type UserDirectoryFixture = {
  id: string;
  person_id?: string;
  last_name: string;
  first_name: string;
  middle_name?: string | null;
};

function usersResponse(
  items: UserDirectoryFixture[],
  pagination: { total?: number; pages?: number } = {},
) {
  return {
    items: items.map((item) => ({
      id: item.id,
      person_id: item.person_id ?? `person-${item.id}`,
      first_name: item.first_name,
      last_name: item.last_name,
      middle_name: item.middle_name ?? null,
    })),
    pagination: {
      page: 1,
      page_size: 20,
      total: pagination.total ?? items.length,
      pages: pagination.pages ?? (items.length ? 1 : 0),
    },
  };
}

function eventDetailResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "ev-1",
    club_id: "club-1",
    event_type: "lesson",
    title: "Ориентирование",
    description: "Занятие по ориентированию",
    start_at: "2026-03-15T17:00:00+03:00",
    end_at: "2026-03-15T19:00:00+03:00",
    timezone: "Europe/Moscow",
    location_type: "custom",
    location_name: "Парк",
    location_address: "ул. Лесная, 5",
    location_latitude: null,
    location_longitude: null,
    status: "published",
    cancellation_reason: null,
    created_by: "u1",
    updated_by: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    group_ids: [],
    instructor_ids: [],
    my_registration_status: null,
    ...overrides,
  };
}

const FIXED_DATE = "2026-03-15"; // Sunday, unrelated to "today" in any timezone this suite runs in.

function fixedRange() {
  return monthRange(new Date(2026, 2, 15));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EventsPage — initial state", () => {
  it("opens on the current month with today selected when the URL carries no date", async () => {
    const today = new Date();
    const range = monthRange(today);
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: "/events" });

    expect(await screen.findByText(monthLabel(today))).toBeInTheDocument();
    await screen.findByRole("grid");
    const todayCell = document.querySelector('[aria-current="date"]');
    expect(todayCell).not.toBeNull();
    expect(todayCell).toHaveAttribute("aria-selected", "true");
  });

  it("restores the selected date from the URL and requests that month's range", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByText("Март 2026")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes(encodeURIComponent(range.from))),
      ).toBe(true);
    });
  });

  it("Today returns to the current month and selects today", async () => {
    const today = new Date();
    const todayRange = monthRange(today);
    const farFuture = new Date(today.getFullYear() + 2, 0, 1);
    const farRange = monthRange(farFuture);

    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(todayRange.from), response: calendarResponse([]) },
      { match: encodeURIComponent(farRange.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, {
      route: `/events?date=${formatDateParam(farFuture)}`,
    });

    expect(await screen.findByText(monthLabel(farFuture))).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Сегодня" }));

    expect(await screen.findByText(monthLabel(today))).toBeInTheDocument();
  });
});

describe("EventsPage — month navigation", () => {
  it("requests the next month's [from,to) range and updates the label", async () => {
    const marchRange = monthRange(new Date(2026, 2, 15));
    const aprilRange = monthRange(new Date(2026, 3, 15));
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(marchRange.from), response: calendarResponse([]) },
      { match: encodeURIComponent(aprilRange.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    await screen.findByText("Март 2026");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Следующий месяц" }));

    expect(await screen.findByText("Апрель 2026")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes(encodeURIComponent(aprilRange.from))),
      ).toBe(true);
    });
  });

  it("keeps the previous range's events on screen while a new range reloads, instead of flashing empty", async () => {
    const range = fixedRange();
    let resolveFiltered: (() => void) | undefined;
    const filteredGate = new Promise<void>((resolve) => {
      resolveFiltered = resolve;
    });

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse([{ id: "g1", name: "Орлы" }]));
      if (url.includes(`from=${encodeURIComponent(range.from)}`) && url.includes("group_id=g1")) {
        await filteredGate;
        return jsonResponse(calendarResponse([]));
      }
      if (url.includes(`from=${encodeURIComponent(range.from)}`)) {
        return jsonResponse(
          calendarResponse([
            calendarItem({
              id: "ev-march",
              title: "Мартовское занятие",
              start_at: "2026-03-15T17:00:00+03:00",
              end_at: "2026-03-15T18:00:00+03:00",
            }),
          ]),
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    await screen.findByText("Мартовское занятие");

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Группа"), "g1");

    // The filtered request hasn't resolved yet, but the previously loaded
    // event must stay visible rather than flashing to an empty state.
    expect(screen.getByText("Мартовское занятие")).toBeInTheDocument();
    expect(screen.queryByText("На этот день событий нет")).not.toBeInTheDocument();
    expect(screen.queryByText("В этом периоде нет событий")).not.toBeInTheDocument();

    resolveFiltered?.();
    await waitFor(() => expect(screen.queryByText("Мартовское занятие")).not.toBeInTheDocument());
    expect(await screen.findByText("В этом периоде нет событий")).toBeInTheDocument();
  });
});

describe("EventsPage — filters", () => {
  it("persists filters across month navigation and Reset clears them", async () => {
    const marchRange = monthRange(new Date(2026, 2, 15));
    const aprilRange = monthRange(new Date(2026, 3, 15));
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      { match: encodeURIComponent(marchRange.from), response: calendarResponse([]) },
      { match: encodeURIComponent(aprilRange.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    await screen.findByText("Март 2026");
    await screen.findByRole("option", { name: "Орлы" });

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Группа"), "g1");

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("group_id=g1"))).toBe(true);
    });

    await user.click(screen.getByRole("button", { name: "Следующий месяц" }));
    await screen.findByText("Апрель 2026");

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input]) =>
            String(input).includes(encodeURIComponent(aprilRange.from)) &&
            String(input).includes("group_id=g1"),
        ),
      ).toBe(true);
    });
    expect(screen.getByLabelText("Группа")).toHaveValue("g1");

    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    expect(screen.getByLabelText("Группа")).toHaveValue("");
    await waitFor(() => {
      const lastCalendarCall = fetchMock.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes("/events/calendar"))
        .at(-1);
      expect(lastCalendarCall).toBeDefined();
      expect(lastCalendarCall).not.toContain("group_id");
    });
  });
});

describe("EventsPage — instructor/user filter (TH-0107)", () => {
  it("opens the picker and loads the instructor directory scoped to role and club", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      {
        match: "/users?",
        response: usersResponse([{ id: "instr-1", last_name: "Кузнецова", first_name: "Ольга" }]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));

    expect(await screen.findByText("Кузнецова Ольга")).toBeInTheDocument();
  });

  it("requests the directory with role=instructor and the current club_id", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input]) => String(input).includes("role=instructor") && String(input).includes("club_id=club-1"),
        ),
      ).toBe(true);
    });
  });

  it("shows a loading state while the directory is being fetched", async () => {
    let resolveUsers: (() => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes("/events/calendar")) return jsonResponse(calendarResponse([]));
      if (url.includes("/users?")) {
        await new Promise<void>((resolve) => {
          resolveUsers = resolve;
        });
        return jsonResponse(usersResponse([]));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));

    expect(await screen.findByText("Загружаем инструкторов…")).toBeInTheDocument();
    resolveUsers?.();
  });

  it("shows an empty state when no instructor matches, and an error state with working Retry otherwise", async () => {
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes("/events/calendar")) return jsonResponse(calendarResponse([]));
      if (url.includes("/users?")) {
        calls += 1;
        if (calls === 1) return jsonResponse(usersResponse([]));
        return jsonResponse(
          { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
          500,
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();

    // Force a refetch (search change) to exercise the error path.
    await user.type(screen.getByLabelText("Поиск инструктора"), "з");
    expect(await screen.findByText("Не удалось загрузить инструкторов")).toBeInTheDocument();

    calls = 0; // next fetch (the Retry click) succeeds again
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("debounces the search field into the `search` query param", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));
    await user.type(screen.getByLabelText("Поиск инструктора"), "Куз");

    await waitFor(
      () => {
        expect(
          fetchMock.mock.calls.some(([input]) => String(input).includes("search=%D0%9A%D1%83%D0%B7")),
        ).toBe(true);
      },
      { timeout: 2000 },
    );
  });

  it("selecting an instructor applies user_id to the calendar query and closes the dialog", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      {
        match: "/users?",
        response: usersResponse([{ id: "instr-1", last_name: "Кузнецова", first_name: "Ольга" }]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Выбрать инструктора" }));
    await user.click(await screen.findByText("Кузнецова Ольга"));

    expect(screen.queryByRole("dialog", { name: "Выбрать инструктора" })).not.toBeInTheDocument();
    expect(await screen.findByText("Кузнецова Ольга")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("user_id=instr-1"))).toBe(true);
    });
  });

  it("«Только мои события» is the same user_id filter, self-scoped — checking it and picking another instructor are mutually exclusive", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse({ userId: "u1" }) },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      {
        match: "/users?",
        response: usersResponse([{ id: "instr-1", last_name: "Кузнецова", first_name: "Ольга" }]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const mineCheckbox = await screen.findByRole("checkbox", { name: "Только мои события" });

    await user.click(mineCheckbox);
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("user_id=u1"))).toBe(true);
    });
    expect(mineCheckbox).toBeChecked();

    // Picking a different instructor supersedes "mine".
    await user.click(screen.getByRole("button", { name: "Изменить" }));
    await user.click(await screen.findByText("Кузнецова Ольга"));
    expect(mineCheckbox).not.toBeChecked();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes("user_id=instr-1"))).toBe(true);
    });
  });

  it("Сбросить clears the selected instructor along with the other filters", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}&user_id=instr-1` });
    await screen.findByText("Инструктор выбран");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Сбросить" }));

    expect(screen.queryByText("Инструктор выбран")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Выбрать инструктора" })).toBeInTheDocument();
    await waitFor(() => {
      const lastCalendarCall = fetchMock.mock.calls
        .map(([input]) => String(input))
        .filter((url) => url.includes("/events/calendar"))
        .at(-1);
      expect(lastCalendarCall).not.toContain("user_id");
    });
  });

  it("persists the selected instructor across month navigation", async () => {
    const marchRange = monthRange(new Date(2026, 2, 15));
    const aprilRange = monthRange(new Date(2026, 3, 15));
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(marchRange.from), response: calendarResponse([]) },
      { match: encodeURIComponent(aprilRange.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, {
      route: `/events?date=${FIXED_DATE}&user_id=instr-1`,
    });
    await screen.findByText("Инструктор выбран");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Следующий месяц" }));
    await screen.findByText("Апрель 2026");

    expect(screen.getByText("Инструктор выбран")).toBeInTheDocument();
  });
});

describe("EventsPage — browser history (ADR-0036 Back/Forward)", () => {
  it("pushes a history entry per month navigation, and Back/Forward restores the exact previous/next state", async () => {
    const range = fixedRange();
    const nextRange = monthRange(new Date(2026, 3, 15));
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: `from=${encodeURIComponent(range.from)}`, response: calendarResponse([]) },
      { match: `from=${encodeURIComponent(nextRange.from)}`, response: calendarResponse([]) },
    ]);

    const { router } = renderWithHistory(<EventsPage />, {
      initialEntries: [`/events?date=${FIXED_DATE}`],
    });

    await screen.findByText("Март 2026");
    expect(router.state.location.search).toContain(`date=${FIXED_DATE}`);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Следующий месяц" }));
    await screen.findByText("Апрель 2026");
    expect(router.state.location.search).toContain(`date=2026-04-15`);

    // Back restores the exact previous state (not a skip or a reset).
    await act(async () => { await router.navigate(-1); });
    expect(await screen.findByText("Март 2026")).toBeInTheDocument();
    expect(router.state.location.search).toContain(`date=${FIXED_DATE}`);

    // Forward restores the exact next state — proving the month change was
    // pushed as a real, distinct entry rather than replacing the current
    // one (a replaced entry would leave nothing for Forward to reach).
    await act(async () => { await router.navigate(1); });
    expect(await screen.findByText("Апрель 2026")).toBeInTheDocument();
    expect(router.state.location.search).toContain(`date=2026-04-15`);
  });

  it("pushes a history entry per filter change and per Reset, restorable via Back", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      { match: "/events/calendar", response: calendarResponse([]) },
    ]);

    const { router } = renderWithHistory(<EventsPage />, {
      initialEntries: [`/events?date=${FIXED_DATE}`],
    });

    await screen.findByRole("option", { name: "Орлы" });
    expect(router.state.location.search).not.toContain("group_id");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Группа"), "g1");
    await waitFor(() => expect(screen.getByLabelText("Группа")).toHaveValue("g1"));
    expect(router.state.location.search).toContain("group_id=g1");

    await user.click(screen.getByRole("button", { name: "Сбросить" }));
    await waitFor(() => expect(screen.getByLabelText("Группа")).toHaveValue(""));
    expect(router.state.location.search).not.toContain("group_id");

    // Back must undo the Reset first (restoring the filter) — Reset is its
    // own history entry, not folded into the filter-change entry.
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(screen.getByLabelText("Группа")).toHaveValue("g1"));
    expect(router.state.location.search).toContain("group_id=g1");

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(screen.getByLabelText("Группа")).toHaveValue(""));
    expect(router.state.location.search).not.toContain("group_id");
  });

  it("pushes a history entry for the instructor/user filter too, restorable via Back", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse({ userId: "u1" }) },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
    ]);

    const { router } = renderWithHistory(<EventsPage />, {
      initialEntries: [`/events?date=${FIXED_DATE}`],
    });

    await screen.findByRole("button", { name: "Выбрать инструктора" });
    expect(router.state.location.search).not.toContain("user_id");

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox", { name: "Только мои события" }));
    await waitFor(() => expect(router.state.location.search).toContain("user_id=u1"));

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(router.state.location.search).not.toContain("user_id"));
    expect(await screen.findByRole("button", { name: "Выбрать инструктора" })).toBeInTheDocument();
  });
});

describe("EventsPage — timezone display", () => {
  it("displays the same wall-clock time for the same instant regardless of its source offset", async () => {
    const range = fixedRange();
    // The exact same instant, expressed with two different UTC offsets.
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-a",
            title: "Событие А",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T18:00:00+03:00",
          }),
          calendarItem({
            id: "ev-b",
            title: "Событие Б",
            start_at: "2026-03-15T14:00:00Z",
            end_at: "2026-03-15T15:00:00Z",
          }),
        ]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    const rowA = await screen.findByText("Событие А");
    const rowB = await screen.findByText("Событие Б");
    const timeA = rowA.closest("button")?.querySelector(`.${styles.eventRowTime}`)?.textContent;
    const timeB = rowB.closest("button")?.querySelector(`.${styles.eventRowTime}`)?.textContent;
    expect(timeA).toBe(timeB);
  });
});

describe("EventsPage — pagination", () => {
  it("loads every page of a multi-page range before rendering the calendar", async () => {
    const range = fixedRange();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from)) && url.includes("page=2")) {
        return jsonResponse(
          calendarResponse(
            [calendarItem({ id: "ev-page2", title: "Со второй страницы", start_at: "2026-03-16T10:00:00+03:00", end_at: "2026-03-16T11:00:00+03:00" })],
            { page: 2, pages: 2, total: 2 },
          ),
        );
      }
      if (url.includes(encodeURIComponent(range.from))) {
        return jsonResponse(
          calendarResponse(
            [calendarItem({ id: "ev-page1", title: "С первой страницы", start_at: "2026-03-15T10:00:00+03:00", end_at: "2026-03-15T11:00:00+03:00" })],
            { page: 1, pages: 2, total: 2 },
          ),
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    await screen.findByText("С первой страницы");

    // Select the 16th to check the second page's item was merged in too.
    const user = userEvent.setup();
    await user.click(screen.getByText("16"));
    expect(await screen.findByText("Со второй страницы")).toBeInTheDocument();

    const calendarCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes("/events/calendar"));
    expect(calendarCalls.length).toBeGreaterThanOrEqual(2);
  });
});

describe("EventsPage — empty states", () => {
  it("shows the empty-range message when the whole month has no events", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByText("В этом периоде нет событий")).toBeInTheDocument();
  });

  it("shows the mobile empty-day message when only the selected day has no events", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("767.98"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({ id: "ev-other-day", title: "Другой день", start_at: "2026-03-20T10:00:00+03:00", end_at: "2026-03-20T11:00:00+03:00" }),
        ]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByText("На этот день событий нет")).toBeInTheDocument();
    expect(screen.queryByText("В этом периоде нет событий")).not.toBeInTheDocument();
  });
});

describe("EventsPage — errors", () => {
  it("shows a friendly error with Retry, and Retry reloads the range", async () => {
    const range = fixedRange();
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from))) {
        calls += 1;
        if (calls === 1) {
          return jsonResponse(
            { error: { code: "internal_error", message: "Не удалось получить события", details: {}, request_id: "r1" } },
            500,
          );
        }
        return jsonResponse(calendarResponse([]));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByText("Не удалось загрузить события")).toBeInTheDocument();
    expect(screen.getByText("Не удалось получить события")).toBeInTheDocument();
    expect(screen.queryByText(/internal_error/)).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Повторить" }));

    expect(await screen.findByText("В этом периоде нет событий")).toBeInTheDocument();
  });

  it("surfaces a create-time authorization error without closing silently or crashing", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: "/events/calendar", response: calendarResponse([]) },
      {
        match: "/events",
        response: { error: { code: "forbidden", message: "Недостаточно прав для создания события", details: {}, request_id: "r1" } },
        status: 403,
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Новое занятие");

    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Недостаточно прав для создания события")).toBeInTheDocument();
    // The dialog must stay open so the user doesn't lose their input.
    expect(screen.getByRole("dialog", { name: "Новое событие" })).toBeInTheDocument();
  });
});

describe("EventsPage — recurring and cancelled visual states", () => {
  it("shows a recurring indicator only for occurrence items, and marks cancelled items without relying on color alone", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-single",
            kind: "event",
            title: "Разовое занятие",
            start_at: "2026-03-15T10:00:00+03:00",
            end_at: "2026-03-15T11:00:00+03:00",
          }),
          calendarItem({
            id: "ev-recurring",
            kind: "occurrence",
            title: "Регулярная тренировка",
            start_at: "2026-03-15T12:00:00+03:00",
            end_at: "2026-03-15T13:00:00+03:00",
            series_id: "series-1",
            series_version: 1,
          }),
          calendarItem({
            id: "ev-cancelled",
            kind: "event",
            title: "Отменённая встреча",
            status: "cancelled",
            cancellation_reason: "Погода",
            start_at: "2026-03-15T14:00:00+03:00",
            end_at: "2026-03-15T15:00:00+03:00",
          }),
        ]),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    const singleRow = (await screen.findByText("Разовое занятие")).closest("button");
    const recurringRow = (await screen.findByText("Регулярная тренировка")).closest("button");
    expect(within(recurringRow as HTMLElement).getByTitle("Повторяющееся событие")).toBeInTheDocument();
    expect(within(singleRow as HTMLElement).queryByTitle("Повторяющееся событие")).not.toBeInTheDocument();

    const cancelledTitle = screen.getByText("Отменённая встреча");
    expect(cancelledTitle).toHaveClass(styles.eventRowTitleCancelled);
    const cancelledRow = cancelledTitle.closest("button") as HTMLElement;
    expect(within(cancelledRow).getByText("Отменено")).toBeInTheDocument();
  });
});

describe("EventsPage — create / edit", () => {
  it("creates an event through the canonical Event API", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/events", response: eventDetailResponse({ id: "new-event", title: "Утренняя тренировка" }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    expect(screen.getByRole("button", { name: "Создать" })).toBeDisabled();

    await user.type(screen.getByLabelText("Название"), "Утренняя тренировка");
    expect(screen.getByRole("button", { name: "Создать" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect(body.title).toBe("Утренняя тренировка");
      expect(body.club_id).toBe("club-1");
      expect(typeof body.timezone).toBe("string");
      expect(body.timezone.length).toBeGreaterThan(0);
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Новое событие" })).not.toBeInTheDocument());
  });

  it("publishes the newly created draft so it appears in the calendar, without a full page reload (TH-0109)", async () => {
    const range = fixedRange();
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });
    let calendarCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes("/events/new-event/status") && method === "POST") {
        return jsonResponse(
          eventDetailResponse({ id: "new-event", title: "Утренняя тренировка", status: "published" }),
        );
      }
      if (url.endsWith("/events") && method === "POST") {
        return jsonResponse(
          eventDetailResponse({ id: "new-event", title: "Утренняя тренировка", status: "draft" }),
        );
      }
      if (url.includes(encodeURIComponent(range.from))) {
        calendarCalls += 1;
        if (calendarCalls === 1) return jsonResponse(calendarResponse([]));
        return jsonResponse(
          calendarResponse([
            calendarItem({
              id: "new-event",
              title: "Утренняя тренировка",
              start_at: "2026-03-15T17:00:00+03:00",
              end_at: "2026-03-15T18:00:00+03:00",
            }),
          ]),
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Утренняя тренировка");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    // The dialog only closes/toasts on the mutation's own onSuccess, which
    // now only fires after the publish step also succeeds.
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Новое событие" })).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Событие создано")).toBeInTheDocument();

    // The calendar range was refetched (not just cached) and now includes
    // the newly published event — proving invalidation actually ran.
    expect(await screen.findByText("Утренняя тренировка")).toBeInTheDocument();
    expect(calendarCalls).toBeGreaterThanOrEqual(2);
    expect(
      fetchMock.mock.calls.some(
        ([reqInput, reqInit]) =>
          String(reqInput).includes("/events/new-event/status") &&
          (reqInit as RequestInit)?.method === "POST" &&
          JSON.parse(String((reqInit as RequestInit).body)).status === "published",
      ),
    ).toBe(true);
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("shows an error and keeps the dialog open when publishing the created event fails (TH-0109)", async () => {
    const range = fixedRange();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from))) return jsonResponse(calendarResponse([]));
      if (url.includes("/events/new-event/status") && method === "POST") {
        return jsonResponse(
          {
            error: {
              code: "internal_error",
              message: "Не удалось опубликовать событие",
              details: {},
              request_id: "r1",
            },
          },
          500,
        );
      }
      if (url.endsWith("/events") && method === "POST") {
        return jsonResponse(eventDetailResponse({ id: "new-event", status: "draft" }));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Утренняя тренировка");
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Не удалось опубликовать событие")).toBeInTheDocument();
    // No false success — the dialog must not close/toast on a failed publish.
    expect(screen.getByRole("dialog", { name: "Новое событие" })).toBeInTheDocument();
    expect(screen.queryByText("Событие создано")).not.toBeInTheDocument();
  });

  it("edits an existing event through PATCH /events/{id}", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));

    const titleInput = await screen.findByLabelText("Название");
    await waitFor(() => expect(titleInput).toHaveValue("Ориентирование"));
    await user.clear(titleInput);
    await user.type(titleInput, "Ориентирование (изменено)");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events/ev-1") && (init as RequestInit)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse(String((patchCall?.[1] as RequestInit).body));
      expect(body.title).toBe("Ориентирование (изменено)");
    });
  });
});

describe("EventsPage — event targeting (TH-0108)", () => {
  it("shows a Group selector, unchecked by default (club-wide)", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/groups?status=active",
        response: groupsResponse([
          { id: "g1", name: "Орлы" },
          { id: "g2", name: "Волки" },
        ]),
      },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));

    expect(await screen.findByRole("checkbox", { name: "Орлы" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Волки" })).not.toBeChecked();
    expect(screen.getByText(/адресовано всем участникам клуба/)).toBeInTheDocument();
  });

  it("creates an event targeted to one selected Group", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
      { match: "/events", response: eventDetailResponse({ id: "new-event", group_ids: ["g1"] }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Занятие");
    await user.click(await screen.findByRole("checkbox", { name: "Орлы" }));
    expect(screen.queryByText(/адресовано всем участникам клуба/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect(body.group_ids).toEqual(["g1"]);
    });
  });

  it("creates an event targeted to several selected Groups", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      {
        match: "/groups?status=active",
        response: groupsResponse([
          { id: "g1", name: "Орлы" },
          { id: "g2", name: "Волки" },
        ]),
      },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
      { match: "/events", response: eventDetailResponse({ id: "new-event", group_ids: ["g1", "g2"] }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Занятие");
    await user.click(await screen.findByRole("checkbox", { name: "Орлы" }));
    await user.click(screen.getByRole("checkbox", { name: "Волки" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect([...body.group_ids].sort()).toEqual(["g1", "g2"]);
    });
  });

  it("selects a single instructor from the picker and sends it on save", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([{ id: "u2", first_name: "Пётр", last_name: "Сидоров" }]) },
      { match: "/events", response: eventDetailResponse({ id: "new-event", instructor_ids: ["u2"] }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Занятие");
    await user.click(screen.getByRole("button", { name: "Добавить инструктора" }));
    await user.click(await screen.findByRole("checkbox", { name: "Сидоров Пётр" }));
    await user.click(screen.getByRole("button", { name: "Готово" }));

    expect(screen.getByText("Сидоров Пётр")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect(body.instructor_ids).toEqual(["u2"]);
    });
  });

  it("selects several instructors from the picker", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      {
        match: "/users?",
        response: usersResponse([
          { id: "u2", first_name: "Пётр", last_name: "Сидоров" },
          { id: "u3", first_name: "Мария", last_name: "Кузнецова" },
        ]),
      },
      { match: "/events", response: eventDetailResponse({ id: "new-event", instructor_ids: ["u2", "u3"] }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Занятие");
    await user.click(screen.getByRole("button", { name: "Добавить инструктора" }));
    await user.click(await screen.findByRole("checkbox", { name: "Сидоров Пётр" }));
    await user.click(screen.getByRole("checkbox", { name: "Кузнецова Мария" }));
    await user.click(screen.getByRole("button", { name: "Готово" }));

    expect(screen.getByText("Сидоров Пётр")).toBeInTheDocument();
    expect(screen.getByText("Кузнецова Мария")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events") && (init as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse(String((postCall?.[1] as RequestInit).body));
      expect([...body.instructor_ids].sort()).toEqual(["u2", "u3"]);
    });
  });

  it("restores existing Group/instructor targeting when editing an Event", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      { match: "/users?", response: usersResponse([{ id: "u2", first_name: "Пётр", last_name: "Сидоров" }]) },
      {
        match: "/events/ev-1",
        response: eventDetailResponse({ group_ids: ["g1"], instructor_ids: ["u2"] }),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByText("Ориентирование"));
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Орлы" })).toBeChecked());
    expect(await screen.findByText("Сидоров Пётр")).toBeInTheDocument();
  });

  it("lets editing change the target Groups and instructors, sent via PATCH", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      { match: "/users?", response: usersResponse([{ id: "u2", first_name: "Пётр", last_name: "Сидоров" }]) },
      {
        match: "/events/ev-1",
        response: eventDetailResponse({ group_ids: ["g1"], instructor_ids: ["u2"] }),
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByText("Ориентирование"));
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));

    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Орлы" })).toBeChecked());
    await user.click(screen.getByRole("checkbox", { name: "Орлы" }));
    await user.click(await screen.findByRole("button", { name: /Убрать/ }));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith("/events/ev-1") && (init as RequestInit)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse(String((patchCall?.[1] as RequestInit).body));
      expect(body.group_ids).toEqual([]);
      expect(body.instructor_ids).toEqual([]);
    });
  });

  it("shows a backend targeting error via toast and keeps the dialog open", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse([{ id: "g1", name: "Орлы" }]) },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
      {
        match: "/events",
        response: {
          error: {
            code: "group_club_mismatch",
            message: "Группа принадлежит другому клубу",
            details: {},
            request_id: "r1",
          },
        },
        status: 422,
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.type(screen.getByLabelText("Название"), "Занятие");
    await user.click(await screen.findByRole("checkbox", { name: "Орлы" }));
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Группа принадлежит другому клубу")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Новое событие" })).toBeInTheDocument();
  });

  it("shows a loading state while the instructor directory loads", async () => {
    const range = fixedRange();
    let resolveUsers: (value: Response) => void = () => {};
    const usersPromise = new Promise<Response>((resolve) => {
      resolveUsers = resolve;
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from))) return jsonResponse(calendarResponse([]));
      if (url.includes("/users?")) return usersPromise;
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.click(screen.getByRole("button", { name: "Добавить инструктора" }));

    expect(await screen.findByText("Загружаем инструкторов…")).toBeInTheDocument();
    resolveUsers(jsonResponse(usersResponse([])));
  });

  it("shows an empty state when the instructor directory has no matches", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
      { match: "/users?", response: usersResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Создать событие" }));
    await user.click(screen.getByRole("button", { name: "Добавить инструктора" }));

    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });
});

describe("EventsPage — participant self-registration (TH-0108.2)", () => {
  it("shows \"Записаться\" for an eligible user, registers on click, and reflects the registered state without a page reload", async () => {
    const range = fixedRange();
    const reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });
    let registrationStatus: string | null = null;
    let eventDetailCalls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from))) {
        return jsonResponse(
          calendarResponse([
            calendarItem({
              id: "ev-1",
              title: "Ориентирование",
              start_at: "2026-03-15T17:00:00+03:00",
              end_at: "2026-03-15T19:00:00+03:00",
            }),
          ]),
        );
      }
      if (url.includes("/events/ev-1/participation") && method === "POST") {
        registrationStatus = "registered";
        return jsonResponse({
          id: "part-1",
          event_id: "ev-1",
          person_id: "person-1",
          registration_status: "registered",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/events/ev-1") && method === "GET") {
        eventDetailCalls += 1;
        return jsonResponse(eventDetailResponse({ my_registration_status: registrationStatus }));
      }
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));
    expect(await screen.findByRole("button", { name: "Записаться" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Записаться" }));

    expect(await screen.findByText("Вы записаны на мероприятие")).toBeInTheDocument();
    await screen.findByText("Вы записаны");
    expect(await screen.findByRole("button", { name: "Отменить запись" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Записаться" })).not.toBeInTheDocument();
    // The dialog re-rendered from a refetched query, not a mutation-local
    // guess — proving invalidation actually ran, without reloading the page.
    expect(eventDetailCalls).toBeGreaterThanOrEqual(2);
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("shows \"Вы записаны\" for an already-registered event and cancels on click, restoring the ability to register again", async () => {
    const range = fixedRange();
    let registrationStatus: string | null = "registered";
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse());
      if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
      if (url.includes(encodeURIComponent(range.from))) {
        return jsonResponse(
          calendarResponse([
            calendarItem({
              id: "ev-1",
              title: "Ориентирование",
              start_at: "2026-03-15T17:00:00+03:00",
              end_at: "2026-03-15T19:00:00+03:00",
            }),
          ]),
        );
      }
      if (url.includes("/events/ev-1/participation") && method === "DELETE") {
        registrationStatus = "cancelled";
        return new Response(null, { status: 204 });
      }
      if (url.includes("/events/ev-1") && method === "GET") {
        return jsonResponse(eventDetailResponse({ my_registration_status: registrationStatus }));
      }
      throw new Error(`Unexpected fetch: ${url} ${method}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));
    expect(await screen.findByText("Вы записаны")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Отменить запись" }));

    expect(await screen.findByText("Запись отменена")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Записаться" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить запись" })).not.toBeInTheDocument();
  });

  it("shows the backend's rejection via toast and keeps \"Записаться\" available for retry when the user is not eligible (e.g. not in the target Group)", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      {
        match: "/events/ev-1/participation",
        response: {
          error: {
            code: "not_eligible_for_event",
            message: "Вы не можете записаться на это мероприятие",
            details: {},
            request_id: "r1",
          },
        },
        status: 403,
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));
    await user.click(await screen.findByRole("button", { name: "Записаться" }));

    expect(await screen.findByText("Вы не можете записаться на это мероприятие")).toBeInTheDocument();
    // No false success, and retry stays available rather than getting stuck.
    expect(await screen.findByRole("button", { name: "Записаться" })).toBeEnabled();
  });

  it("shows no self-registration action for a non-published event (e.g. draft)", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Черновик",
            status: "draft",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      { match: "/events/ev-1", response: eventDetailResponse({ status: "draft" }) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    const [dayListTitle] = await screen.findAllByText("Черновик");
    await user.click(dayListTitle);
    await screen.findByRole("button", { name: "Редактировать" });
    expect(screen.queryByRole("button", { name: "Записаться" })).not.toBeInTheDocument();
    expect(screen.queryByText("Вы записаны")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить запись" })).not.toBeInTheDocument();
  });
});

describe("EventsPage — responsive composition", () => {
  it("renders the distinct mobile day-navigation composition instead of the month grid", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("767.98"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    await screen.findByText("Март 2026");
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Предыдущий день" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Следующий день" })).toBeInTheDocument();
  });

  it("renders the month grid on desktop widths", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(range.from), response: calendarResponse([]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByRole("grid")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Предыдущий день" })).not.toBeInTheDocument();
  });
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
