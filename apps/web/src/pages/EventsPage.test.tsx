import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { EventsPage } from "./EventsPage";
import styles from "./EventsPage.module.css";
import { renderWithProviders, renderWithHistory, stubFetch } from "../test/renderWithProviders";
import { formatDateParam, monthLabel, monthGridRange } from "../domain/calendarDate";

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
  return monthGridRange(new Date(2026, 2, 15));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EventsPage — initial state", () => {
  it("opens on the current month with today selected when the URL carries no date", async () => {
    const today = new Date();
    const range = monthGridRange(today);
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
    const todayRange = monthGridRange(today);
    const farFuture = new Date(today.getFullYear() + 2, 0, 1);
    const farRange = monthGridRange(farFuture);

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
    const marchRange = monthGridRange(new Date(2026, 2, 15));
    const aprilRange = monthGridRange(new Date(2026, 3, 15));
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
    const marchRange = monthGridRange(new Date(2026, 2, 15));
    const aprilRange = monthGridRange(new Date(2026, 3, 15));
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
    const marchRange = monthGridRange(new Date(2026, 2, 15));
    const aprilRange = monthGridRange(new Date(2026, 3, 15));
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
    const nextRange = monthGridRange(new Date(2026, 3, 15));
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

describe("EventsPage — first displayed week / adjacent-month events (Issue #212)", () => {
  // 1 September 2026 is a Tuesday, so the September grid's first week
  // starts on Monday 31 August — a previous-month date.
  const SEPTEMBER_DATE = "2026-09-15";
  const aug31Start = new Date(2026, 7, 31, 18, 0);
  const aug31End = new Date(2026, 7, 31, 20, 0);
  const aug31Item = {
    id: "ev-aug31",
    title: "Вечерний сбор",
    start_at: aug31Start.toISOString(),
    end_at: aug31End.toISOString(),
  };

  it.each(["admin", "instructor", "member", "guardian"])(
    "%s: keeps the first week visible and shows the 31 August event while viewing September",
    async (roleCode) => {
      const septemberRange = monthGridRange(new Date(2026, 8, 15));
      const fetchMock = stubFetch([
        { match: "/auth/me", response: meResponse({ roleCode }) },
        { match: "/groups?status=active", response: groupsResponse() },
        { match: encodeURIComponent(septemberRange.from), response: calendarResponse([aug31Item]) },
      ]);

      renderWithProviders(<EventsPage />, { route: `/events?date=${SEPTEMBER_DATE}` });

      expect(await screen.findByText("Сентябрь 2026")).toBeInTheDocument();
      const grid = await screen.findByRole("grid");
      const firstCell = within(grid).getAllByRole("gridcell")[0];
      // The adjacent-month cell is rendered (styled as outside), not hidden.
      expect(firstCell).toHaveClass(styles.monthCellOutside);
      expect(within(firstCell).getByText("31")).toBeInTheDocument();
      expect(await within(firstCell).findByText(/Вечерний сбор/)).toBeInTheDocument();

      // The request covers the whole grid, starting at local midnight of 31 August.
      const calendarCall = fetchMock.mock.calls
        .map(([input]) => String(input))
        .find((url) => url.includes("/events/calendar"));
      expect(calendarCall).toBeDefined();
      const from = new URL(calendarCall!, "http://localhost").searchParams.get("from");
      expect(formatDateParam(new Date(from!))).toBe("2026-08-31");
    },
  );

  it("selecting the adjacent-month cell opens that day's events", async () => {
    const septemberRange = monthGridRange(new Date(2026, 8, 15));
    const augustRange = monthGridRange(new Date(2026, 7, 31));
    stubFetch([
      { match: "/auth/me", response: meResponse({ roleCode: "member" }) },
      { match: "/groups?status=active", response: groupsResponse() },
      { match: encodeURIComponent(septemberRange.from), response: calendarResponse([aug31Item]) },
      { match: encodeURIComponent(augustRange.from), response: calendarResponse([aug31Item]) },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${SEPTEMBER_DATE}` });

    const grid = await screen.findByRole("grid");
    const firstCell = within(grid).getAllByRole("gridcell")[0];
    await within(firstCell).findByText(/Вечерний сбор/);

    const user = userEvent.setup();
    await user.click(firstCell);

    const row = await screen.findByText("Вечерний сбор", { selector: `.${styles.eventRowTitle}` });
    expect(row.closest("button")).toBeInTheDocument();
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

// --- Competition document package workflow (TH-0117 / Issue #175) ---------

function requirementFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: "req1",
    event_id: "ev-1",
    document_type: "medical_certificate",
    required: true,
    ...overrides,
  };
}

function requirementsCollection(items: Array<Record<string, unknown>>) {
  return { items, pagination: { page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0 } };
}

async function openDocumentPackageDialog(
  user: ReturnType<typeof userEvent.setup>,
): Promise<HTMLElement> {
  await user.click(await screen.findByText("Ориентирование"));
  await user.click(await screen.findByRole("button", { name: "Документы для соревнования" }));
  return screen.findByRole("dialog", { name: "Документы для соревнования" });
}

type DocRoute = (url: string, method: string, init?: RequestInit) => Response | undefined;

function matrixParticipant(overrides: Record<string, unknown> = {}) {
  return {
    person_id: "p-1",
    first_name: "Иван",
    last_name: "Алексеев",
    middle_name: null,
    requirements: [{ document_type: "medical_certificate", required: true, result: "valid" }],
    ...overrides,
  };
}

function matrixResponse(participants: Array<Record<string, unknown>>) {
  return { event_id: "ev-1", participants };
}

/** One Event on FIXED_DATE plus the «Документы для соревнования»
 * endpoints. `route` may answer any request first (method-aware); the
 * defaults below cover the rest. Matrix before the requirement list:
 * both URLs share the `/document-requirements` prefix. */
function stubDocumentsWorkflow({
  role = "admin",
  matrix = matrixResponse([matrixParticipant()]),
  requirements = [requirementFixture()],
  route,
}: {
  role?: string;
  matrix?: unknown;
  requirements?: Array<Record<string, unknown>>;
  route?: DocRoute;
} = {}) {
  const range = fixedRange();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const custom = route?.(url, method, init);
    if (custom) return custom;
    if (url.includes("/auth/me")) return jsonResponse(meResponse({ roleCode: role }));
    if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
    if (url.includes(encodeURIComponent(range.from))) {
      return jsonResponse(
        calendarResponse([
          calendarItem({ id: "ev-1", title: "Ориентирование", start_at: "2026-03-15T17:00:00+03:00", end_at: "2026-03-15T19:00:00+03:00" }),
        ]),
      );
    }
    if (url.includes("/events/ev-1/document-requirements/matrix") && method === "GET") return jsonResponse(matrix);
    if (url.includes("/events/ev-1/document-requirements") && method === "GET") {
      return jsonResponse(requirementsCollection(requirements));
    }
    if (url.endsWith("/events/ev-1")) return jsonResponse(eventDetailResponse());
    throw new Error(`Unexpected fetch: ${url} ${method}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: ReturnType<typeof vi.fn>, fragment: string, method = "GET") {
  return fetchMock.mock.calls.filter(
    ([input, init]) => String(input).includes(fragment) && ((init as RequestInit | undefined)?.method ?? "GET") === method,
  );
}

function readinessSection() {
  return screen.findByRole("region", { name: "Готовность участников" });
}

function errorResponse(status: number, code: string, message: string, details: unknown = {}) {
  return jsonResponse({ error: { code, message, details, request_id: "r1" } }, status);
}

describe("EventsPage — «Документы для соревнования»: access (Issue #175)", () => {
  it("offers the workflow to Administrator", async () => {
    stubDocumentsWorkflow();
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    expect(await openDocumentPackageDialog(user)).toBeInTheDocument();
  });

  it.each([["instructor"], ["member"], ["guardian"]])("does not offer the workflow to %s", async (role) => {
    stubDocumentsWorkflow({ role });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));

    expect(await screen.findByRole("dialog", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Документы для соревнования" })).not.toBeInTheDocument();
  });

  it("does not offer the workflow for a single recurring occurrence (EventDocumentRequirement is Event-scoped, not per-occurrence)", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "occ-1",
            kind: "occurrence",
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
            series_id: "series-1",
          }),
        ]),
      },
      {
        match: "/events/occurrences/occ-1",
        response: {
          id: "occ-1",
          series_id: "series-1",
          club_id: "club-1",
          name: "Ориентирование",
          description: null,
          event_type: "lesson",
          starts_at: "2026-03-15T17:00:00+03:00",
          ends_at: "2026-03-15T19:00:00+03:00",
          timezone: "Europe/Moscow",
          status: "published",
          cancellation_reason: null,
        },
      },
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await user.click(await screen.findByText("Ориентирование"));

    expect(screen.queryByRole("button", { name: "Документы для соревнования" })).not.toBeInTheDocument();
  });
});

describe("EventsPage — «Документы для соревнования»: readiness matrix (events-api.md §31.4)", () => {
  it("renders every participant x requirement result exactly as the backend returns it, from one matrix request", async () => {
    const fetchMock = stubDocumentsWorkflow({
      matrix: matrixResponse([
        matrixParticipant({
          person_id: "p-1",
          last_name: "Алексеев",
          first_name: "Иван",
          requirements: [
            { document_type: "insurance", required: false, result: "missing" },
            { document_type: "medical_certificate", required: true, result: "valid" },
          ],
        }),
        matrixParticipant({
          person_id: "p-2",
          last_name: "Борисова",
          first_name: "Анна",
          middle_name: "Петровна",
          requirements: [
            { document_type: "insurance", required: false, result: "valid" },
            { document_type: "medical_certificate", required: true, result: "expired" },
          ],
        }),
      ]),
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    const first = await within(section).findByRole("list", { name: "Документы: Алексеев Иван" });
    const firstCells = within(first).getAllByRole("listitem");
    expect(firstCells[0]).toHaveTextContent("insurance");
    expect(firstCells[0]).toHaveTextContent("Опционально");
    expect(firstCells[0]).toHaveTextContent("Отсутствует");
    expect(firstCells[1]).toHaveTextContent("Медицинская справка");
    expect(firstCells[1]).toHaveTextContent("Действителен");

    const second = within(section).getByRole("list", { name: "Документы: Борисова Анна Петровна" });
    const secondCells = within(second).getAllByRole("listitem");
    expect(secondCells[0]).toHaveTextContent("Действителен");
    expect(secondCells[1]).toHaveTextContent("Медицинская справка");
    expect(secondCells[1]).toHaveTextContent("Истёк");

    // Backend order is kept: Алексеев before Борисова.
    const links = within(section).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["Алексеев Иван", "Борисова Анна Петровна"]);

    // One readiness source: no per-participant check, no Person/Document lookups.
    expect(callsTo(fetchMock, "/events/ev-1/document-requirements/matrix")).toHaveLength(1);
    expect(callsTo(fetchMock, "/document-requirements/p-")).toHaveLength(0);
    expect(callsTo(fetchMock, "/persons")).toHaveLength(0);
  });

  it("shows a calm empty state when the Event has no registered participants", async () => {
    stubDocumentsWorkflow({ matrix: matrixResponse([]) });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    expect(await within(section).findByText("Нет зарегистрированных участников")).toBeInTheDocument();
    expect(within(section).queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows «Требования к документам не заданы» instead of an empty matrix when the Event has no requirements", async () => {
    stubDocumentsWorkflow({
      requirements: [],
      matrix: matrixResponse([matrixParticipant({ requirements: [] })]),
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    expect(await within(section).findByText("Требования к документам не заданы")).toBeInTheDocument();
    expect(within(section).queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows the forbidden state for 403 without a retry", async () => {
    stubDocumentsWorkflow({
      route: (url) =>
        url.includes("/document-requirements/matrix") ? errorResponse(403, "forbidden", "Нет прав") : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    expect(await within(section).findByText("Нет доступа к документам участников")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });

  it("shows the existence-hiding not-found state for 404", async () => {
    stubDocumentsWorkflow({
      route: (url) =>
        url.includes("/document-requirements/matrix") ? errorResponse(404, "not_found", "Event not found") : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    expect(await within(section).findByText("Мероприятие не найдено")).toBeInTheDocument();
  });

  it("offers a retry after an API failure and renders the matrix once it succeeds", async () => {
    let attempts = 0;
    stubDocumentsWorkflow({
      route: (url) => {
        if (!url.includes("/document-requirements/matrix")) return undefined;
        attempts += 1;
        return attempts === 1
          ? errorResponse(500, "internal_error", "Server error")
          : jsonResponse(matrixResponse([matrixParticipant()]));
      },
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    expect(await within(section).findByText("Не удалось загрузить готовность участников")).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Повторить" }));

    expect(await within(section).findByRole("link", { name: "Алексеев Иван" })).toBeInTheDocument();
    expect(attempts).toBe(2);
  });
});

describe("EventsPage — «Документы для соревнования»: participant → Person → Документы", () => {
  it("links a participant to the existing Person Documents tab and Back returns to the Event", async () => {
    stubDocumentsWorkflow();
    const { router } = renderWithHistory(
      <Routes>
        <Route path="/events" element={<EventsPage />} />
        <Route path="/people/:personId" element={<p>Карточка человека</p>} />
      </Routes>,
      { initialEntries: [`/events?date=${FIXED_DATE}`] },
    );
    const user = userEvent.setup();
    await openDocumentPackageDialog(user);
    const section = await readinessSection();

    const link = await within(section).findByRole("link", { name: "Алексеев Иван" });
    expect(link).toHaveAttribute("href", "/people/p-1?tab=documents");

    await user.click(link);

    expect(await screen.findByText("Карточка человека")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/people/p-1");
    expect(router.state.location.search).toBe("?tab=documents");

    await act(async () => {
      await router.navigate(-1);
    });

    expect(router.state.location.search).toContain("event=ev-1");
    expect(await screen.findByRole("dialog", { name: "Ориентирование" })).toBeInTheDocument();
  });
});

describe("EventsPage — «Документы для соревнования»: requirements", () => {
  it("lists existing requirements, creates one and refreshes the matrix", async () => {
    let requirements = [requirementFixture()];
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method, init) => {
        if (url.includes("/events/ev-1/document-requirements") && method === "POST") {
          const body = JSON.parse(String(init!.body));
          const created = requirementFixture({ id: "req2", document_type: body.document_type, required: body.required });
          requirements = [...requirements, created];
          return jsonResponse(created, 201);
        }
        if (url.includes("/events/ev-1/document-requirements") && !url.includes("matrix") && method === "GET") {
          return jsonResponse(requirementsCollection(requirements));
        }
        return undefined;
      },
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);
    await readinessSection();

    expect(await within(dialog).findByRole("checkbox", { name: "Обязательно" })).toBeChecked();
    const matrixCallsBefore = callsTo(fetchMock, "/document-requirements/matrix").length;

    await user.click(within(dialog).getByRole("button", { name: "Добавить требование" }));
    const addDialog = await screen.findByRole("dialog", { name: "Добавить требование" });
    await user.type(within(addDialog).getByLabelText("Тип документа"), "insurance_waiver");
    await user.click(within(addDialog).getByRole("checkbox", { name: "Обязательно для допуска" }));
    await user.click(within(addDialog).getByRole("button", { name: "Добавить" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Добавить требование" })).not.toBeInTheDocument();
    });
    expect(await within(dialog).findByText("insurance_waiver")).toBeInTheDocument();
    const [, postInit] = callsTo(fetchMock, "/events/ev-1/document-requirements", "POST")[0];
    expect(JSON.parse(String((postInit as RequestInit).body))).toEqual({
      document_type: "insurance_waiver",
      required: false,
    });
    await waitFor(() => {
      expect(callsTo(fetchMock, "/document-requirements/matrix").length).toBeGreaterThan(matrixCallsBefore);
    });
  });

  it("does not block a duplicate type itself and shows the backend 409 duplicate_document_requirement", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method) =>
        url.includes("/events/ev-1/document-requirements") && method === "POST"
          ? errorResponse(409, "duplicate_document_requirement", "Requirement already exists")
          : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);
    await readinessSection();

    await user.click(within(dialog).getByRole("button", { name: "Добавить требование" }));
    const addDialog = await screen.findByRole("dialog", { name: "Добавить требование" });
    await user.type(within(addDialog).getByLabelText("Тип документа"), "medical_certificate");
    const submit = within(addDialog).getByRole("button", { name: "Добавить" });
    expect(submit).toBeEnabled();
    await user.click(submit);

    expect(await screen.findByText("Требование для этого типа документа уже существует")).toBeInTheDocument();
    expect(callsTo(fetchMock, "/events/ev-1/document-requirements", "POST")).toHaveLength(1);
    expect(screen.getByRole("dialog", { name: "Добавить требование" })).toBeInTheDocument();
  });

  it("toggles required through PATCH", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method, init) =>
        url.includes("/events/ev-1/document-requirements/req1") && method === "PATCH"
          ? jsonResponse(requirementFixture({ required: JSON.parse(String(init!.body)).required }))
          : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);
    await readinessSection();

    await user.click(await within(dialog).findByRole("checkbox", { name: "Обязательно" }));

    await waitFor(() => {
      expect(callsTo(fetchMock, "/events/ev-1/document-requirements/req1", "PATCH")).toHaveLength(1);
    });
    const [, patchInit] = callsTo(fetchMock, "/events/ev-1/document-requirements/req1", "PATCH")[0];
    expect(JSON.parse(String((patchInit as RequestInit).body))).toEqual({ required: false });
  });

  it("deletes a requirement through the confirm dialog", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method) =>
        url.includes("/events/ev-1/document-requirements/req1") && method === "DELETE"
          ? new Response(null, { status: 204 })
          : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);
    await readinessSection();

    await user.click(await within(dialog).findByRole("button", { name: "Удалить" }));
    const confirmDialog = screen.getByRole("dialog", { name: "Удалить требование?" });
    await user.click(within(confirmDialog).getByRole("button", { name: "Удалить" }));

    await waitFor(() => {
      expect(callsTo(fetchMock, "/events/ev-1/document-requirements/req1", "DELETE")).toHaveLength(1);
    });
  });
});

describe("EventsPage — «Документы для соревнования»: package", () => {
  function zipResponse() {
    return new Response(new Blob(["zip content"]), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": 'attachment; filename="competition-documents-event.zip"',
      },
    });
  }

  function incompleteResponse() {
    return errorResponse(409, "document_package_incomplete", "The document package is incomplete", {
      incomplete: [
        { participant_display_name: "Петров Иван", document_type: "medical_certificate", result: "missing", required: true },
        { participant_display_name: "Сидорова Мария", document_type: "insurance", result: "expired", required: false },
      ],
    });
  }

  function packageBodies(fetchMock: ReturnType<typeof vi.fn>) {
    return callsTo(fetchMock, "/events/ev-1/document-package", "POST").map(([, init]) =>
      JSON.parse(String((init as RequestInit).body)),
    );
  }

  function mockDownload() {
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    (URL as unknown as { createObjectURL: () => string }).createObjectURL = vi.fn(() => "blob:mock-url");
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = vi.fn();
    return anchorClick;
  }

  it("forms a complete package and downloads the backend ZIP", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method) => (url.endsWith("/events/ev-1/document-package") && method === "POST" ? zipResponse() : undefined),
    });
    const anchorClick = mockDownload();
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Сформировать пакет документов" }));

    expect(await screen.findByText("Пакет документов сформирован")).toBeInTheDocument();
    expect(packageBodies(fetchMock)).toEqual([{ confirm_incomplete: false }]);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    anchorClick.mockRestore();
  });

  it("shows the incomplete-package warning with backend details; cancel sends nothing more", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method) =>
        url.endsWith("/events/ev-1/document-package") && method === "POST" ? incompleteResponse() : undefined,
    });
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Сформировать пакет документов" }));

    const warning = await screen.findByRole("dialog", { name: "Не все документы готовы" });
    expect(within(warning).getByText(/Требуют внимания: 2/)).toBeInTheDocument();
    expect(within(warning).getByText("Петров Иван")).toBeInTheDocument();
    expect(within(warning).getByText("Медицинская справка · Обязательно")).toBeInTheDocument();
    expect(within(warning).getByText("Отсутствует")).toBeInTheDocument();
    expect(within(warning).getByText("Сидорова Мария")).toBeInTheDocument();
    expect(within(warning).getByText("insurance · Опционально")).toBeInTheDocument();
    expect(within(warning).getByText("Истёк")).toBeInTheDocument();

    await user.click(within(warning).getByRole("button", { name: "Отмена" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Не все документы готовы" })).not.toBeInTheDocument();
    });
    expect(packageBodies(fetchMock)).toEqual([{ confirm_incomplete: false }]);
  });

  it("re-sends with confirm_incomplete=true only after explicit confirmation", async () => {
    const fetchMock = stubDocumentsWorkflow({
      route: (url, method, init) => {
        if (!(url.endsWith("/events/ev-1/document-package") && method === "POST")) return undefined;
        return JSON.parse(String(init!.body)).confirm_incomplete ? zipResponse() : incompleteResponse();
      },
    });
    const anchorClick = mockDownload();
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const user = userEvent.setup();
    const dialog = await openDocumentPackageDialog(user);

    await user.click(within(dialog).getByRole("button", { name: "Сформировать пакет документов" }));
    const warning = await screen.findByRole("dialog", { name: "Не все документы готовы" });
    expect(packageBodies(fetchMock)).toEqual([{ confirm_incomplete: false }]);

    await user.click(within(warning).getByRole("button", { name: "Всё равно сформировать пакет" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Не все документы готовы" })).not.toBeInTheDocument();
    });
    expect(packageBodies(fetchMock)).toEqual([{ confirm_incomplete: false }, { confirm_incomplete: true }]);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    anchorClick.mockRestore();
  });
});

describe("EventsPage — Participant Export contextual action (TH-0118.5)", () => {
  function stubEvent(roleCode: string, kind: "event" | "occurrence" = "event") {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse({ roleCode }) },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: kind === "event" ? "ev-1" : "occ-1",
            kind,
            title: "Ориентирование",
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
            series_id: kind === "occurrence" ? "series-1" : null,
          }),
        ]),
      },
      {
        match: "/events/occurrences/occ-1",
        response: {
          id: "occ-1",
          series_id: "series-1",
          club_id: "club-1",
          name: "Ориентирование",
          description: null,
          event_type: "lesson",
          starts_at: "2026-03-15T17:00:00+03:00",
          ends_at: "2026-03-15T19:00:00+03:00",
          timezone: "Europe/Moscow",
          status: "published",
          cancellation_reason: null,
        },
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
  }

  it("offers Administrator an export of this Event's participants, and no Event import", async () => {
    stubEvent("admin");
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));

    expect(await screen.findByRole("link", { name: "Экспорт участников" })).toHaveAttribute(
      "href",
      "/reports/export?context=event&event_id=ev-1",
    );
    expect(screen.queryByRole("link", { name: /Импорт/ })).not.toBeInTheDocument();
  });

  it.each([["instructor"], ["member"], ["guardian"]])("does not offer the export to %s", async (role) => {
    stubEvent(role);
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));

    expect(await screen.findByRole("dialog", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Экспорт участников" })).not.toBeInTheDocument();
  });

  it("does not offer the export for a single recurring occurrence", async () => {
    stubEvent("admin", "occurrence");
    const user = userEvent.setup();

    await user.click(await screen.findByText("Ориентирование"));

    expect(await screen.findByRole("dialog", { name: "Ориентирование" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Экспорт участников" })).not.toBeInTheDocument();
  });
});

describe("EventsPage — deep link from a News item (TH-0120 / Issue #227)", () => {
  it("lands on the Event's day and opens that Event's detail", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse({ roleCode: "guardian" }) },
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

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}&event=ev-1` });

    expect(await screen.findByRole("dialog", { name: "Ориентирование" })).toBeInTheDocument();
  });

  it("opens nothing for an id the backend calendar does not return", async () => {
    const range = fixedRange();
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse({ roleCode: "member" }) },
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
    ]);

    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}&event=hidden` });

    expect(await screen.findByText("Ориентирование")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/events/hidden"))).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

// --- Manual status control (Issue #281 / ADR-0018) --------------------------

type LifecycleRequest = { url: string; method: string; body: unknown };

/** A stateful backend stub for one Event: GET returns the current status,
 * POST /status and /archive apply the requested transition (or the
 * configured rejection) and every request is recorded. */
function mockLifecycleApi({
  status,
  rejection,
}: {
  status: string;
  rejection?: { code: string; message: string; status: number };
}) {
  const range = fixedRange();
  const state = { status, cancellation_reason: null as string | null };
  const requests: LifecycleRequest[] = [];
  let calendarCalls = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    requests.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/auth/me")) return jsonResponse(meResponse());
    if (url.includes("/groups?status=active")) return jsonResponse(groupsResponse());
    if (url.includes(encodeURIComponent(range.from))) {
      calendarCalls += 1;
      return jsonResponse(
        calendarResponse([
          calendarItem({
            id: "ev-1",
            title: "Ориентирование",
            status,
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      );
    }
    if (method === "POST" && (url.endsWith("/events/ev-1/status") || url.endsWith("/events/ev-1/archive"))) {
      if (rejection) {
        return jsonResponse(
          { error: { code: rejection.code, message: rejection.message, details: {}, request_id: "r1" } },
          rejection.status,
        );
      }
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, string>) : {};
      state.status = url.endsWith("/archive") ? "archived" : body.status;
      state.cancellation_reason = body.cancellation_reason ?? state.cancellation_reason;
      return jsonResponse(eventDetailResponse({ ...state }));
    }
    if (url.includes("/events/ev-1") && method === "GET") {
      return jsonResponse(eventDetailResponse({ ...state }));
    }
    throw new Error(`Unexpected fetch: ${url} ${method}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { requests, calendarCalls: () => calendarCalls };
}

async function openLifecycleEvent(title = "Ориентирование") {
  renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
  const user = userEvent.setup();
  const [entry] = await screen.findAllByText(title);
  await user.click(entry);
  const dialog = await screen.findByRole("dialog");
  return { user, dialog };
}

const ALL_STATUS_ACTION_LABELS = ["Опубликовать", "Начать", "Завершить", "Отменить", "Архивировать"];

describe("EventsPage — manual status control (Issue #281)", () => {
  it.each([
    ["draft", ["Опубликовать"]],
    ["published", ["Начать", "Отменить"]],
    ["in_progress", ["Завершить", "Отменить"]],
    ["completed", ["Архивировать"]],
    ["cancelled", ["Архивировать"]],
  ])("shows only the canonical actions for a %s Event", async (status, expected) => {
    mockLifecycleApi({ status });
    const { dialog } = await openLifecycleEvent();
    const group = await within(dialog).findByRole("group", { name: "Статус события" });
    const labels = within(group)
      .getAllByRole("button")
      .map((button) => button.textContent?.trim());
    expect(labels).toEqual(expected);
    for (const label of ALL_STATUS_ACTION_LABELS.filter((label) => !expected.includes(label))) {
      expect(within(group).queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
  });

  it("shows no status actions for an archived Event", async () => {
    mockLifecycleApi({ status: "archived" });
    const { dialog } = await openLifecycleEvent();
    await within(dialog).findByText("Парк, ул. Лесная, 5");
    expect(within(dialog).getByText("В архиве")).toBeInTheDocument();
    expect(within(dialog).queryByRole("group", { name: "Статус события" })).not.toBeInTheDocument();
    for (const label of ALL_STATUS_ACTION_LABELS) {
      expect(within(dialog).queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
  });

  it("starts a published Event through POST /status and refreshes the displayed status and calendar", async () => {
    const api = mockLifecycleApi({ status: "published" });
    const { user, dialog } = await openLifecycleEvent();
    const calendarCallsBefore = api.calendarCalls();

    await user.click(await within(dialog).findByRole("button", { name: "Начать" }));

    expect(await within(dialog).findByText("Идёт сейчас")).toBeInTheDocument();
    expect(api.requests).toContainEqual({
      url: "/api/v1/events/ev-1/status",
      method: "POST",
      body: { status: "in_progress" },
    });
    // The new status's own actions replace the old ones.
    expect(await within(dialog).findByRole("button", { name: "Завершить" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Начать" })).not.toBeInTheDocument();
    await waitFor(() => expect(api.calendarCalls()).toBeGreaterThan(calendarCallsBefore));
  });

  it("completes an in-progress Event manually", async () => {
    const api = mockLifecycleApi({ status: "in_progress" });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Завершить" }));

    expect(await within(dialog).findByText("Завершено")).toBeInTheDocument();
    expect(api.requests).toContainEqual({
      url: "/api/v1/events/ev-1/status",
      method: "POST",
      body: { status: "completed" },
    });
    expect(await within(dialog).findByRole("button", { name: "Архивировать" })).toBeInTheDocument();
  });

  it("archives through the dedicated archive endpoint", async () => {
    const api = mockLifecycleApi({ status: "completed" });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Архивировать" }));

    expect(await within(dialog).findByText("В архиве")).toBeInTheDocument();
    expect(api.requests.some((r) => r.method === "POST" && r.url === "/api/v1/events/ev-1/archive")).toBe(true);
    expect(api.requests.some((r) => r.method === "POST" && r.url.endsWith("/status"))).toBe(false);
    expect(within(dialog).queryByRole("group", { name: "Статус события" })).not.toBeInTheDocument();
  });

  it("asks for a cancellation reason, never sends an empty one, and shows the cancelled state", async () => {
    const api = mockLifecycleApi({ status: "published" });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Отменить" }));
    const reasonDialog = await screen.findByRole("dialog", { name: "Отменить событие" });
    const submit = within(reasonDialog).getByRole("button", { name: "Отменить событие" });
    expect(submit).toBeDisabled();

    await user.type(within(reasonDialog).getByLabelText("Причина отмены"), "   ");
    expect(submit).toBeDisabled();
    expect(api.requests.some((r) => r.method === "POST")).toBe(false);

    await user.clear(within(reasonDialog).getByLabelText("Причина отмены"));
    await user.type(within(reasonDialog).getByLabelText("Причина отмены"), "  Штормовое предупреждение ");
    await user.click(submit);

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Отменить событие" })).not.toBeInTheDocument(),
    );
    expect(api.requests).toContainEqual({
      url: "/api/v1/events/ev-1/status",
      method: "POST",
      body: { status: "cancelled", cancellation_reason: "Штормовое предупреждение" },
    });
    expect(await within(dialog).findByText("Отменено")).toBeInTheDocument();
    expect(within(dialog).getByText("Причина отмены: Штормовое предупреждение")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Архивировать" })).toBeInTheDocument();
  });

  it("closing the reason dialog sends nothing", async () => {
    const api = mockLifecycleApi({ status: "in_progress" });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Отменить" }));
    const reasonDialog = await screen.findByRole("dialog", { name: "Отменить событие" });
    await user.click(within(reasonDialog).getByRole("button", { name: "Отмена" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Отменить событие" })).not.toBeInTheDocument(),
    );
    expect(api.requests.some((r) => r.method === "POST")).toBe(false);
    expect(within(dialog).getByText("Идёт сейчас")).toBeInTheDocument();
  });

  it("shows the backend's rejection of a transition and keeps the current status", async () => {
    mockLifecycleApi({
      status: "published",
      rejection: {
        code: "invalid_status_transition",
        message: "'completed' -> 'in_progress' is not an allowed transition",
        status: 409,
      },
    });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Начать" }));

    expect(
      await screen.findByText("'completed' -> 'in_progress' is not an allowed transition"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Запланировано")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Начать" })).toBeEnabled();
  });

  it("shows the backend's cancellation error inside the reason dialog", async () => {
    mockLifecycleApi({
      status: "published",
      rejection: { code: "not_found", message: "Event not found", status: 404 },
    });
    const { user, dialog } = await openLifecycleEvent();

    await user.click(await within(dialog).findByRole("button", { name: "Отменить" }));
    const reasonDialog = await screen.findByRole("dialog", { name: "Отменить событие" });
    await user.type(within(reasonDialog).getByLabelText("Причина отмены"), "Нет инструктора");
    await user.click(within(reasonDialog).getByRole("button", { name: "Отменить событие" }));

    expect(await within(reasonDialog).findByRole("alert")).toHaveTextContent("Event not found");
    expect(screen.getByRole("dialog", { name: "Отменить событие" })).toBeInTheDocument();
    expect(within(dialog).getByText("Запланировано")).toBeInTheDocument();
  });

  it("offers no status actions for a recurring occurrence (the status endpoint is Event-only)", async () => {
    const range = fixedRange();
    stubFetch([
      { match: "/auth/me", response: meResponse() },
      { match: "/groups?status=active", response: groupsResponse() },
      {
        match: encodeURIComponent(range.from),
        response: calendarResponse([
          calendarItem({
            id: "occ-1",
            kind: "occurrence",
            title: "Вечерняя серия",
            series_id: "series-1",
            series_version: 1,
            start_at: "2026-03-15T17:00:00+03:00",
            end_at: "2026-03-15T19:00:00+03:00",
          }),
        ]),
      },
      {
        match: "/events/occurrences/occ-1",
        response: {
          id: "occ-1",
          series_id: "series-1",
          club_id: "club-1",
          name: "Вечерняя серия",
          description: null,
          event_type: "training",
          starts_at: "2026-03-15T17:00:00+03:00",
          ends_at: "2026-03-15T19:00:00+03:00",
          timezone: "Europe/Moscow",
          status: "scheduled",
          cancellation_reason: null,
        },
      },
    ]);
    const { dialog } = await openLifecycleEvent("Вечерняя серия");
    await within(dialog).findByRole("button", { name: "Редактировать" });
    expect(within(dialog).queryByRole("group", { name: "Статус события" })).not.toBeInTheDocument();
  });
});

// --- Issue #299: «Участники» tab and role-aware Event UI ---------------------

function participantsResponse(
  items: Array<{ person_id: string; last_name: string; first_name: string; middle_name?: string | null }>,
  pagination: { page?: number; page_size?: number; total?: number; pages?: number } = {},
) {
  const total = pagination.total ?? items.length;
  return {
    items: items.map((item) => ({ middle_name: null, ...item })),
    pagination: {
      page: pagination.page ?? 1,
      page_size: pagination.page_size ?? 50,
      total,
      pages: pagination.pages ?? (total ? 1 : 0),
    },
  };
}

function eventCalendarHandlers(roleCode: string, kind: "event" | "occurrence" = "event") {
  const range = fixedRange();
  return [
    { match: "/auth/me", response: meResponse({ roleCode }) },
    { match: "/groups?status=active", response: groupsResponse() },
    {
      match: encodeURIComponent(range.from),
      response: calendarResponse([
        calendarItem({
          id: kind === "event" ? "ev-1" : "occ-1",
          kind,
          title: "Ориентирование",
          start_at: "2026-03-15T17:00:00+03:00",
          end_at: "2026-03-15T19:00:00+03:00",
          series_id: kind === "occurrence" ? "series-1" : null,
        }),
      ]),
    },
    {
      match: "/events/occurrences/occ-1",
      response: {
        id: "occ-1",
        series_id: "series-1",
        club_id: "club-1",
        name: "Ориентирование",
        description: null,
        event_type: "lesson",
        starts_at: "2026-03-15T17:00:00+03:00",
        ends_at: "2026-03-15T19:00:00+03:00",
        timezone: "Europe/Moscow",
        status: "published",
        cancellation_reason: null,
      },
    },
  ];
}

async function openEvent() {
  const user = userEvent.setup();
  await user.click(await screen.findByText("Ориентирование"));
  const dialog = await screen.findByRole("dialog", { name: "Ориентирование" });
  return { user, dialog };
}

function participantsRequests(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.includes("/participants"));
}

describe("EventsPage — «Участники» tab (Issue #299)", () => {
  it.each([["admin"], ["instructor"]])(
    "%s sees the backend roster of registered participants with its backend count",
    async (role) => {
      const fetchMock = stubFetch([
        ...eventCalendarHandlers(role),
        {
          match: "/events/ev-1/participants",
          response: participantsResponse([
            { person_id: "p-1", last_name: "Алексеев", first_name: "Иван", middle_name: "Петрович" },
            { person_id: "p-2", last_name: "Борисова", first_name: "Анна" },
          ]),
        },
        { match: "/events/ev-1", response: eventDetailResponse() },
      ]);
      renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
      const { user, dialog } = await openEvent();

      // The roster is not fetched until the tab is opened.
      expect(participantsRequests(fetchMock)).toEqual([]);
      await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

      expect(await within(dialog).findByText("Зарегистрировано: 2")).toBeInTheDocument();
      const rows = within(dialog).getAllByRole("listitem").map((item) => item.textContent);
      expect(rows).toEqual(["Алексеев Иван Петрович", "Борисова Анна"]);
      // One backend page, never a wider set: the canonical endpoint with
      // backend pagination and no client-side status filter.
      expect(participantsRequests(fetchMock)).toEqual([
        "/api/v1/events/ev-1/participants?page=1&page_size=50",
      ]);
    },
  );

  it("shows only the minimal Person projection — no contacts or documents", async () => {
    stubFetch([
      ...eventCalendarHandlers("admin"),
      {
        match: "/events/ev-1/participants",
        response: participantsResponse([{ person_id: "p-1", last_name: "Алексеев", first_name: "Иван" }]),
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    const panel = await within(dialog).findByRole("tabpanel");
    await within(panel).findByText("Алексеев Иван");
    expect(within(panel).queryByRole("link")).not.toBeInTheDocument();
    expect(within(panel).queryByText(/телефон|email|документ/i)).not.toBeInTheDocument();
  });

  it("pages through the roster with the backend pagination", async () => {
    const fetchMock = stubFetch([
      ...eventCalendarHandlers("admin"),
      {
        match: "/events/ev-1/participants?page=2",
        response: participantsResponse([{ person_id: "p-51", last_name: "Юдин", first_name: "Юрий" }], {
          page: 2,
          total: 51,
          pages: 2,
        }),
      },
      {
        match: "/events/ev-1/participants?page=1",
        response: participantsResponse(
          Array.from({ length: 50 }, (_, index) => ({
            person_id: `p-${index + 1}`,
            last_name: `Участник${String(index + 1).padStart(2, "0")}`,
            first_name: "Тест",
          })),
          { total: 51, pages: 2 },
        ),
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    expect(await within(dialog).findByText("Зарегистрировано: 51")).toBeInTheDocument();
    expect(within(dialog).getByText("Страница 1 из 2 · всего 51")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Далее" }));

    expect(await within(dialog).findByText("Юдин Юрий")).toBeInTheDocument();
    expect(within(dialog).getByText("Страница 2 из 2 · всего 51")).toBeInTheDocument();
    expect(participantsRequests(fetchMock)).toEqual([
      "/api/v1/events/ev-1/participants?page=1&page_size=50",
      "/api/v1/events/ev-1/participants?page=2&page_size=50",
    ]);
  });

  it("shows an empty state when nobody is registered", async () => {
    stubFetch([
      ...eventCalendarHandlers("instructor"),
      { match: "/events/ev-1/participants", response: participantsResponse([]) },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    expect(await within(dialog).findByText("Нет зарегистрированных участников")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Зарегистрировано:/)).not.toBeInTheDocument();
  });

  it("shows the backend's existence-hiding 404 as an unavailable roster, without retry", async () => {
    stubFetch([
      ...eventCalendarHandlers("instructor"),
      {
        match: "/events/ev-1/participants",
        status: 404,
        response: { error: { code: "not_found", message: "Event not found", details: {}, request_id: "r" } },
      },
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    expect(await within(dialog).findByText("Список участников недоступен")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });

  it("offers a retry after a server failure", async () => {
    let failing = true;
    const range = fixedRange();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
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
        if (url.includes("/events/ev-1/participants")) {
          return failing
            ? jsonResponse({ error: { code: "internal_error", message: "Сбой", details: {}, request_id: "r" } }, 500)
            : jsonResponse(participantsResponse([{ person_id: "p-1", last_name: "Алексеев", first_name: "Иван" }]));
        }
        if (url.includes("/events/ev-1")) return jsonResponse(eventDetailResponse());
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    expect(await within(dialog).findByText("Не удалось загрузить участников")).toBeInTheDocument();
    failing = false;
    await user.click(within(dialog).getByRole("button", { name: "Повторить" }));
    expect(await within(dialog).findByText("Алексеев Иван")).toBeInTheDocument();
  });

  it.each([["member"], ["guardian"]])(
    "%s gets no «Участники» tab and the roster is never requested",
    async (role) => {
      const fetchMock = stubFetch([
        ...eventCalendarHandlers(role),
        { match: "/events/ev-1", response: eventDetailResponse() },
      ]);
      renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
      const { dialog } = await openEvent();

      expect(await within(dialog).findByRole("button", { name: "Записаться" })).toBeInTheDocument();
      expect(within(dialog).queryByRole("tab", { name: "Участники" })).not.toBeInTheDocument();
      expect(within(dialog).queryByRole("tablist")).not.toBeInTheDocument();
      expect(participantsRequests(fetchMock)).toEqual([]);
    },
  );

  it("offers no roster for a recurring occurrence (not an event_id of the participants API)", async () => {
    const fetchMock = stubFetch(eventCalendarHandlers("admin", "occurrence"));
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { dialog } = await openEvent();

    expect(within(dialog).queryByRole("tab", { name: "Участники" })).not.toBeInTheDocument();
    expect(participantsRequests(fetchMock)).toEqual([]);
  });

  it("refreshes the roster from the backend after the user's own self-registration", async () => {
    const range = fixedRange();
    let registered = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/auth/me")) return jsonResponse(meResponse({ roleCode: "instructor" }));
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
        registered = true;
        return jsonResponse({
          id: "part-1",
          event_id: "ev-1",
          person_id: "person-1",
          registration_status: "registered",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        });
      }
      if (url.includes("/events/ev-1/participants")) {
        return jsonResponse(
          participantsResponse(
            registered ? [{ person_id: "person-1", last_name: "Иванова", first_name: "Анна" }] : [],
          ),
        );
      }
      if (url.includes("/events/ev-1")) {
        return jsonResponse(eventDetailResponse({ my_registration_status: registered ? "registered" : null }));
      }
      throw new Error(`Unexpected fetch: ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();

    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));
    expect(await within(dialog).findByText("Нет зарегистрированных участников")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("tab", { name: "Обзор" }));
    await user.click(await within(dialog).findByRole("button", { name: "Записаться" }));
    await within(dialog).findByText("Вы записаны");
    await user.click(within(dialog).getByRole("tab", { name: "Участники" }));

    expect(await within(dialog).findByText("Иванова Анна")).toBeInTheDocument();
  });
});

describe("EventsPage — role-aware management controls (Issue #299)", () => {
  it.each([["admin"], ["instructor"]])("%s keeps Event management and instructor filters", async (role) => {
    stubFetch([
      ...eventCalendarHandlers(role),
      { match: "/events/ev-1", response: eventDetailResponse() },
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    expect(await screen.findByRole("button", { name: "Создать событие" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выбрать инструктора" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Только мои события" })).toBeInTheDocument();

    const { dialog } = await openEvent();
    expect(within(dialog).getByRole("button", { name: "Редактировать" })).toBeInTheDocument();
    expect(await within(dialog).findByRole("group", { name: "Статус события" })).toBeInTheDocument();
  });

  it.each([["member"], ["guardian"]])(
    "%s sees no management actions or instructor-management filters, but can still self-register",
    async (role) => {
      stubFetch([
        ...eventCalendarHandlers(role),
        { match: "/events/ev-1", response: eventDetailResponse() },
      ]);
      renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

      await screen.findByText("Ориентирование");
      expect(screen.queryByRole("button", { name: "Создать событие" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Выбрать инструктора" })).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: "Только мои события" })).not.toBeInTheDocument();
      // Ordinary calendar filters stay available.
      expect(screen.getByRole("combobox", { name: "Тип" })).toBeInTheDocument();

      const { dialog } = await openEvent();
      expect(await within(dialog).findByRole("button", { name: "Записаться" })).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "Редактировать" })).not.toBeInTheDocument();
      expect(within(dialog).queryByRole("group", { name: "Статус события" })).not.toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: /Начать|Завершить|Отменить событие|Архивировать/ })).not.toBeInTheDocument();
    },
  );
});

// --- Issue #305 / ADR-0047: lesson attendance ---------------------------------

type AttendanceRowFixture = { id: string; name: string; status: "present" | "absent" | null };

function attendanceResponse(rows: AttendanceRowFixture[], total = rows.length) {
  const marked = rows.filter((row) => row.status !== null).length;
  const present = rows.filter((row) => row.status === "present").length;
  const absent = rows.filter((row) => row.status === "absent").length;
  return {
    items: rows.map((row) => {
      const [last_name, first_name] = row.name.split(" ");
      return {
        person: { id: row.id, first_name, last_name, middle_name: null },
        status: row.status,
        absence_reason: null,
        comment: null,
      };
    }),
    pagination: { page: 1, page_size: 50, total: rows.length, pages: rows.length ? 1 : 0 },
    summary: { total, marked, present, absent, unmarked: total - marked },
  };
}

function lessonCalendarHandlers(roleCode: string, attendanceByEvent: Record<string, unknown>) {
  const range = fixedRange();
  return [
    { match: "/auth/me", response: meResponse({ roleCode }) },
    { match: "/groups?status=active", response: groupsResponse() },
    ...Object.entries(attendanceByEvent).map(([eventId, response]) => ({
      match: `/events/${eventId}/attendance`,
      response,
    })),
    {
      match: encodeURIComponent(range.from),
      response: calendarResponse([
        { id: "les-present", title: "Урок присутствовал", start_at: "2026-03-15T10:00:00+03:00", end_at: "2026-03-15T11:00:00+03:00" },
        { id: "les-absent", title: "Урок отсутствовал", start_at: "2026-03-15T12:00:00+03:00", end_at: "2026-03-15T13:00:00+03:00" },
        // Next day: a cell shows at most two chips.
        { id: "les-unmarked", title: "Урок не отмечен", start_at: "2026-03-16T14:00:00+03:00", end_at: "2026-03-16T15:00:00+03:00" },
        {
          id: "training-1",
          event_type: "training",
          title: "Тренировка",
          start_at: "2026-03-16T16:00:00+03:00",
          end_at: "2026-03-16T17:00:00+03:00",
        },
      ]),
    },
  ];
}

function chipFor(title: string): HTMLElement {
  const chip = within(screen.getByRole("grid"))
    .getAllByText((_, element) => Boolean(element?.className.includes(styles.chip) && element.textContent?.includes(title)))
    .at(0);
  if (!chip) throw new Error(`No chip for ${title}`);
  return chip;
}

function rowFor(title: string): HTMLElement {
  const row = screen.getByText(title).closest("button");
  if (!row) throw new Error(`No row for ${title}`);
  return row;
}

function attendanceRequests(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.includes("/attendance"));
}

describe("EventsPage — lesson attendance in the calendar (Issue #305)", () => {
  it("Member: present is green, absent is red, unmarked stays neutral", async () => {
    const fetchMock = stubFetch(
      lessonCalendarHandlers("member", {
        "les-present": attendanceResponse([{ id: "me", name: "Иванова Анна", status: "present" }]),
        "les-absent": attendanceResponse([{ id: "me", name: "Иванова Анна", status: "absent" }]),
        "les-unmarked": attendanceResponse([{ id: "me", name: "Иванова Анна", status: null }]),
      }),
    );
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    await waitFor(() =>
      expect(chipFor("Урок присутствовал")).toHaveClass(styles.chipAttendancePresent),
    );
    await waitFor(() => expect(chipFor("Урок отсутствовал")).toHaveClass(styles.chipAttendanceAbsent));
    const neutral = chipFor("Урок не отмечен");
    expect(neutral).not.toHaveClass(styles.chipAttendancePresent);
    expect(neutral).not.toHaveClass(styles.chipAttendanceAbsent);

    expect(within(rowFor("Урок присутствовал")).getByText("Посещаемость: присутствовал")).toBeInTheDocument();
    expect(within(rowFor("Урок отсутствовал")).getByText("Посещаемость: отсутствовал")).toBeInTheDocument();
    // Unmarked is never rendered as absent (nor labelled at all).
    expect(screen.queryByText("Посещаемость: не отмечено")).not.toBeInTheDocument();

    // Only lessons query attendance, through the existing endpoint.
    expect(attendanceRequests(fetchMock).sort()).toEqual([
      "/api/v1/events/les-absent/attendance?page=1&page_size=100",
      "/api/v1/events/les-present/attendance?page=1&page_size=100",
      "/api/v1/events/les-unmarked/attendance?page=1&page_size=100",
    ]);
  });

  it("Guardian: aggregates the visible children with present > absent > unmarked", async () => {
    stubFetch(
      lessonCalendarHandlers("guardian", {
        // Mixed present + absent children -> green.
        "les-present": attendanceResponse([
          { id: "c1", name: "Петров Иван", status: "absent" },
          { id: "c2", name: "Петрова Мария", status: "present" },
        ]),
        // No present child, one absent -> red.
        "les-absent": attendanceResponse([
          { id: "c1", name: "Петров Иван", status: "absent" },
          { id: "c2", name: "Петрова Мария", status: null },
        ]),
        // All accessible children unmarked -> neutral.
        "les-unmarked": attendanceResponse([
          { id: "c1", name: "Петров Иван", status: null },
          { id: "c2", name: "Петрова Мария", status: null },
        ]),
      }),
    );
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    await waitFor(() =>
      expect(chipFor("Урок присутствовал")).toHaveClass(styles.chipAttendancePresent),
    );
    await waitFor(() => expect(chipFor("Урок отсутствовал")).toHaveClass(styles.chipAttendanceAbsent));
    const neutral = chipFor("Урок не отмечен");
    expect(neutral).not.toHaveClass(styles.chipAttendancePresent);
    expect(neutral).not.toHaveClass(styles.chipAttendanceAbsent);
    // No participant names or per-child details leak into the calendar.
    expect(screen.queryByText(/Петров/)).not.toBeInTheDocument();
  });

  it.each([["admin"], ["instructor"]])(
    "%s sees a marked/total summary, never one color for a mixed roster",
    async (role) => {
      const mixed = attendanceResponse(
        [
          ...Array.from({ length: 10 }, (_, index) => ({ id: `p${index}`, name: "Участник А", status: "present" as const })),
          ...Array.from({ length: 5 }, (_, index) => ({ id: `q${index}`, name: "Участник Б", status: "absent" as const })),
        ],
        18,
      );
      stubFetch(lessonCalendarHandlers(role, { "les-present": mixed }));
      renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

      const row = await screen.findByText("Урок присутствовал");
      await waitFor(() =>
        expect(within(row.closest("button") as HTMLElement).getByText("15/18")).toBeInTheDocument(),
      );
      expect(within(rowFor("Урок присутствовал")).getByText("Отмечено 15 из 18")).toBeInTheDocument();
      const chip = chipFor("Урок присутствовал");
      expect(chip).not.toHaveClass(styles.chipAttendancePresent);
      expect(chip).not.toHaveClass(styles.chipAttendanceAbsent);
    },
  );

  it("shows no indication where the backend refuses attendance", async () => {
    stubFetch([
      ...lessonCalendarHandlers("member", {}).slice(0, 2),
      { match: "/attendance", response: { detail: "Event not found" }, status: 404 },
      ...lessonCalendarHandlers("member", {}).slice(2),
    ]);
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });

    await screen.findByText("Урок присутствовал");
    const chip = chipFor("Урок присутствовал");
    expect(chip).not.toHaveClass(styles.chipAttendancePresent);
    expect(chip).not.toHaveClass(styles.chipAttendanceAbsent);
    expect(screen.queryByText(/Посещаемость:/)).not.toBeInTheDocument();
  });
});

describe("EventsPage — «Посещаемость» tab (Issue #305)", () => {
  const roster = attendanceResponse([
    { id: "p-1", name: "Алексеев Иван", status: null },
    { id: "p-2", name: "Борисова Анна", status: "absent" },
  ]);

  function attendanceHandlers(role: string, detail = eventDetailResponse()) {
    return [
      ...eventCalendarHandlers(role),
      { match: "/events/ev-1/attendance", response: roster },
      { match: "/events/ev-1", response: detail },
    ];
  }

  function writes(fetchMock: ReturnType<typeof stubFetch>) {
    return fetchMock.mock.calls
      .filter(([, init]) => init?.method && init.method !== "GET")
      .map(([input, init]) => ({ url: String(input), method: init?.method, body: JSON.parse(String(init?.body ?? "null")) }));
  }

  it("is not offered to Member or Guardian", async () => {
    for (const role of ["member", "guardian"]) {
      stubFetch(attendanceHandlers(role));
      const { unmount } = renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
      const { dialog } = await openEvent();
      expect(within(dialog).queryByRole("tab", { name: "Посещаемость" })).not.toBeInTheDocument();
      unmount();
      vi.unstubAllGlobals();
    }
  });

  it.each([["admin"], ["instructor"]])(
    "%s sees the roster with unmarked distinct from absent and marks through the existing PUT",
    async (role) => {
      const fetchMock = stubFetch(attendanceHandlers(role));
      renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
      const { user, dialog } = await openEvent();
      await user.click(within(dialog).getByRole("tab", { name: "Посещаемость" }));

      const panel = await within(dialog).findByRole("tabpanel");
      expect(await within(panel).findByText("Отмечено 1 из 2")).toBeInTheDocument();
      const first = within(panel).getByText("Алексеев Иван").closest("li") as HTMLElement;
      const second = within(panel).getByText("Борисова Анна").closest("li") as HTMLElement;
      expect(within(first).getByText("Не отмечен")).toBeInTheDocument();
      expect(within(second).getAllByText("Отсутствовал").length).toBeGreaterThan(0);

      await user.click(within(panel).getByRole("button", { name: "Алексеев Иван: присутствовал" }));
      await waitFor(() =>
        expect(writes(fetchMock)).toContainEqual({
          url: "/api/v1/events/ev-1/attendance/p-1",
          method: "PUT",
          body: { status: "present" },
        }),
      );

      await user.click(within(panel).getByRole("button", { name: "Отметить неотмеченных присутствующими" }));
      await waitFor(() =>
        expect(writes(fetchMock)).toContainEqual({
          url: "/api/v1/events/ev-1/attendance",
          method: "PUT",
          body: { items: [{ person_id: "p-1", status: "present" }] },
        }),
      );
    },
  );

  it("routes a completed lesson through the correction workflow with a reason", async () => {
    const fetchMock = stubFetch(attendanceHandlers("admin", eventDetailResponse({ status: "completed" })));
    renderWithProviders(<EventsPage />, { route: `/events?date=${FIXED_DATE}` });
    const { user, dialog } = await openEvent();
    await user.click(within(dialog).getByRole("tab", { name: "Посещаемость" }));
    const panel = await within(dialog).findByRole("tabpanel");
    await within(panel).findByText("Отмечено 1 из 2");

    // No normal marking for a completed occurrence, and no correction for
    // an unmarked participant (a correction never creates a record).
    expect(within(panel).queryByRole("button", { name: /: присутствовал$/ })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Алексеев Иван: исправить отметку" })).not.toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Борисова Анна: исправить отметку" }));
    const correction = await screen.findByRole("dialog", { name: "Исправить отметку" });
    const save = within(correction).getByRole("button", { name: "Сохранить" });
    expect(save).toBeDisabled();
    await user.type(within(correction).getByLabelText("Причина исправления"), "Ошибка при отметке");
    await user.click(save);

    await waitFor(() =>
      expect(writes(fetchMock)).toContainEqual({
        url: "/api/v1/events/ev-1/attendance/p-2/corrections",
        method: "POST",
        body: { status: "present", reason: "Ошибка при отметке" },
      }),
    );
  });
});
