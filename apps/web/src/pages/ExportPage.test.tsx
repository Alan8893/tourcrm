import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { ExportPage } from "./ExportPage";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi, type MockApiHandler } from "../test/mockApi";
import { printHtmlBlob, saveBlob } from "../api/client";
import exportPageSource from "./ExportPage.tsx?raw";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, saveBlob: vi.fn(), printHtmlBlob: vi.fn() };
});

/** The backend allowlist as `GET /memberships/exports/fields` returns it.
 * `person.shoe_size` is deliberately not a real canonical field: it proves
 * the wizard renders whatever the backend returns, with no frontend copy
 * of the allowlist. */
const FIELDS = {
  items: [
    { field_code: "person.last_name", label: "Фамилия", contexts: ["club", "group", "event", "group_event"] },
    { field_code: "person.first_name", label: "Имя", contexts: ["club", "group", "event", "group_event"] },
    { field_code: "person.birth_date", label: "Дата рождения", contexts: ["club", "group", "event", "group_event"] },
    { field_code: "person.phone", label: "Телефон", contexts: ["club", "group", "event", "group_event"] },
    { field_code: "group.name", label: "Группа", contexts: ["group", "group_event"] },
    { field_code: "event.name", label: "Мероприятие", contexts: ["event", "group_event"] },
    { field_code: "guardian.phone", label: "Телефон представителя", contexts: ["club", "group", "event", "group_event"] },
    { field_code: "person.shoe_size", label: "Размер обуви", contexts: ["club"] },
  ],
};

const GROUPS = {
  items: [
    {
      id: "g1",
      club_id: "club-1",
      name: "5Б",
      description: null,
      status: "active",
      valid_from: "2026-01-01T00:00:00Z",
      valid_to: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  pagination: { page: 1, page_size: 50, total: 1, pages: 1 },
};

const EVENTS = {
  items: [
    {
      id: "ev-1",
      club_id: "club-1",
      event_type: "trip",
      title: "Поход на Эльбрус",
      description: null,
      start_at: "2026-10-10T08:00:00Z",
      end_at: "2026-10-12T18:00:00Z",
      timezone: "Europe/Moscow",
      status: "published",
    },
  ],
  pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
};

/** `GET /memberships/exports/filters`. The labels (and the second value)
 * deliberately differ from anything the frontend could know, proving the
 * options come from the backend rather than a local list. */
const FILTERS = {
  participation_status: [
    { value: "registered", label: "Записан (backend)" },
    { value: "backend_only_status", label: "Статус только с backend" },
  ],
};

function exportBackend(extra: MockApiHandler[] = [], exportHandler?: Partial<MockApiHandler>) {
  return mockApi([
    ...extra,
    { method: "GET", match: "/memberships/exports/fields", body: FIELDS },
    { method: "GET", match: "/memberships/exports/filters", body: FILTERS },
    { method: "POST", match: "/memberships/exports", body: {}, ...exportHandler },
    { method: "GET", match: "/groups", body: GROUPS },
    { method: "GET", match: "/events/ev-1", body: { ...EVENTS.items[0], group_ids: [], instructor_ids: [] } },
    { method: "GET", match: "/events?", body: EVENTS },
  ]);
}

function exportRequestBody(fetchMock: ReturnType<typeof mockApi>) {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => String(input).endsWith("/memberships/exports") && init?.method === "POST",
  );
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

function renderExport(route = "/reports/export") {
  return renderWithProviders(
    <Routes>
      <Route path="/reports/export" element={<ExportPage />} />
    </Routes>,
    { route },
  );
}

type User = ReturnType<typeof userEvent.setup>;

async function next(user: User) {
  await user.click(screen.getByRole("button", { name: "Далее" }));
}

/** dataset → fields → (default filters) → format → summary. */
async function configureClubExport(user: User, format: "XLSX" | "PDF" | "Печать") {
  await user.click(screen.getByRole("radio", { name: /Все участники/ }));
  await next(user);
  await user.click(await screen.findByRole("checkbox", { name: "Фамилия" }));
  await user.click(screen.getByRole("checkbox", { name: "Имя" }));
  await user.click(screen.getByRole("checkbox", { name: "Телефон" }));
  await next(user);
  await next(user);
  await user.click(screen.getByRole("radio", { name: new RegExp(`^${format}`) }));
  await next(user);
}

beforeEach(() => {
  vi.mocked(saveBlob).mockClear();
  vi.mocked(printHtmlBlob).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ExportPage — master wizard", () => {
  it("starts with dataset selection and requires a choice before continuing", async () => {
    exportBackend();
    renderExport();

    expect(screen.getByRole("heading", { name: "Экспорт участников" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Шаги экспорта" });
    expect(within(steps).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "1Что выгружаем?",
      "2Поля",
      "3Фильтры",
      "4Формат",
      "5Экспорт",
    ]);
    ["Все участники", "Участники группы", "Участники события", "Группа на событии"].forEach((name) =>
      expect(screen.getByRole("radio", { name: new RegExp(name) })).toBeInTheDocument(),
    );
    // Single-club product: no club selector anywhere.
    expect(screen.queryByLabelText(/Клуб/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
  });

  it("loads fields from the backend and offers only those available for the chosen dataset", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);

    expect(await screen.findByRole("checkbox", { name: "Размер обуви" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Телефон представителя" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Группа" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Мероприятие" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships/exports/fields")),
    ).toBe(true);

    // Multi-select: nothing selected → cannot continue.
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Выбрать все" }));
    expect(screen.getByText("Выбрано: 6 из 6")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Снять все" }));
    expect(screen.getByText("Выбрано: 0 из 6")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Фамилия" }));
    await user.click(screen.getByRole("checkbox", { name: "Размер обуви" }));
    expect(screen.getByText("Выбрано: 2 из 6")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Далее" })).toBeEnabled();
  });

  it("shows a loading state and then a backend error for the field list", async () => {
    mockApi([
      {
        method: "GET",
        match: "/memberships/exports/fields",
        status: 500,
        body: { error: { code: "internal_error", message: "Сервер недоступен" } },
      },
    ]);
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);

    expect(await screen.findByText("Не удалось загрузить поля")).toBeInTheDocument();
    expect(screen.getByText("Сервер недоступен")).toBeInTheDocument();
  });

  it("renders the forbidden state when the backend denies the field list", async () => {
    mockApi([
      {
        method: "GET",
        match: "/memberships/exports/fields",
        status: 403,
        body: { error: { code: "forbidden", message: "Forbidden" } },
      },
    ]);
    renderExport();

    expect(await screen.findByText("Экспорт недоступен")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Все участники/ })).not.toBeInTheDocument();
  });

  it("exports XLSX with one request built from the chosen dataset, fields and filters", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);
    await user.click(await screen.findByRole("checkbox", { name: "Телефон" }));
    await user.click(screen.getByRole("checkbox", { name: "Фамилия" }));
    await next(user);

    // Club dataset: only the membership-status filter applies.
    expect(screen.queryByLabelText("Группа")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Статус участия в событии")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Статус членства в клубе"), "suspended");
    await next(user);

    expect(screen.getByRole("radio", { name: /^XLSX/ })).toBeChecked();
    await next(user);

    const summary = screen.getByLabelText("Параметры экспорта");
    expect(within(summary).getByText("Все участники")).toBeInTheDocument();
    // Backend order is kept, not click order.
    expect(within(summary).getByText("Фамилия, Телефон")).toBeInTheDocument();
    expect(within(summary).getByText("Приостановлено")).toBeInTheDocument();
    expect(within(summary).getByText("XLSX")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveBlob).mock.calls[0][1]).toBe("participants.xlsx");
    expect(exportRequestBody(fetchMock)).toEqual({
      context: "club",
      fields: ["person.last_name", "person.phone"],
      format: "xlsx",
      membership_status: "suspended",
    });
    expect(await screen.findByText("Файл сформирован и сохранён.")).toBeInTheDocument();
    expect(printHtmlBlob).not.toHaveBeenCalled();
  });

  it("exports PDF with the same request shape", async () => {
    const fetchMock = exportBackend([], {
      headers: { "Content-Disposition": 'attachment; filename="participants-20260930-1000.pdf"' },
    });
    renderExport();
    const user = userEvent.setup();

    await configureClubExport(user, "PDF");
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveBlob).mock.calls[0][1]).toBe("participants-20260930-1000.pdf");
    expect(exportRequestBody(fetchMock)).toEqual({
      context: "club",
      fields: ["person.last_name", "person.first_name", "person.phone"],
      format: "pdf",
      membership_status: "active",
    });
  });

  it("opens Print from the same request shape", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await configureClubExport(user, "Печать");
    await user.click(screen.getByRole("button", { name: "Открыть печать" }));

    await waitFor(() => expect(printHtmlBlob).toHaveBeenCalledTimes(1));
    expect(saveBlob).not.toHaveBeenCalled();
    expect(exportRequestBody(fetchMock)).toEqual({
      context: "club",
      fields: ["person.last_name", "person.first_name", "person.phone"],
      format: "print",
      membership_status: "active",
    });
  });

  it("shows a backend validation error without saving anything", async () => {
    exportBackend([], {
      status: 422,
      body: {
        error: {
          code: "export_field_not_available",
          message: "One or more fields are not available in this export context",
        },
      },
    });
    renderExport();
    const user = userEvent.setup();

    await configureClubExport(user, "XLSX");
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    expect(await screen.findByText(/Не удалось сформировать экспорт/)).toHaveTextContent(
      "One or more fields are not available in this export context",
    );
    expect(saveBlob).not.toHaveBeenCalled();
  });

  it("requires a group for the group dataset and sends only its applicable filters", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Участники группы/ }));
    await next(user);
    await user.click(await screen.findByRole("checkbox", { name: "Группа" }));
    await next(user);

    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    expect(screen.queryByLabelText("Статус участия в событии")).not.toBeInTheDocument();
    const status = screen.getByLabelText("Статус в группе");
    expect(within(status).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Активные",
      "Завершённые",
    ]);
    await user.selectOptions(await screen.findByLabelText("Группа"), "g1");
    await next(user);
    await next(user);

    expect(within(screen.getByLabelText("Параметры экспорта")).getByText("«5Б»")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).toEqual({
      context: "group",
      group_id: "g1",
      fields: ["group.name"],
      format: "xlsx",
      membership_status: "active",
    });
  });

  it("passes Group + Event to the backend as a context — never an intersection computed here", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Группа на событии/ }));
    await next(user);
    await user.click(await screen.findByRole("checkbox", { name: "Мероприятие" }));
    await next(user);

    await user.selectOptions(await screen.findByLabelText("Группа"), "g1");
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.click(await screen.findByRole("button", { name: /Поход на Эльбрус/ }));
    await user.selectOptions(
      await screen.findByLabelText("Статус участия в событии"),
      "backend_only_status",
    );
    await next(user);
    await next(user);
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).toEqual({
      context: "group_event",
      group_id: "g1",
      event_id: "ev-1",
      participation_status: "backend_only_status",
      fields: ["event.name"],
      format: "xlsx",
      membership_status: "active",
    });
    // No membership/participation lists were fetched to intersect locally.
    expect(
      fetchMock.mock.calls.some(([input]) => /\/groups\/g1\/members|participation/.test(String(input))),
    ).toBe(false);
  });
});

describe("ExportPage — contextual entry points", () => {
  it("opens from a Group with the Group pre-selected", async () => {
    const fetchMock = exportBackend();
    renderExport("/reports/export?context=group&group_id=g1");
    const user = userEvent.setup();

    // Context is known: the wizard starts at field selection.
    expect(await screen.findByRole("checkbox", { name: "Группа" })).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Фамилия" }));
    await next(user);
    expect(await screen.findByLabelText("Группа")).toHaveValue("g1");
    await next(user);
    await next(user);
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).toMatchObject({ context: "group", group_id: "g1" });
  });

  it("opens from an Event with the Event pre-selected and allows the Group + Event variant", async () => {
    const fetchMock = exportBackend();
    renderExport("/reports/export?context=event&event_id=ev-1");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Мероприятие" }));
    await next(user);
    expect(await screen.findByText("Поход на Эльбрус", { selector: "strong" })).toBeInTheDocument();

    // Switch to Group + Event: the Event stays selected, a Group is added.
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await user.click(screen.getByRole("radio", { name: /Группа на событии/ }));
    await next(user);
    await next(user);
    await user.selectOptions(await screen.findByLabelText("Группа"), "g1");
    await next(user);
    await next(user);
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).toMatchObject({
      context: "group_event",
      group_id: "g1",
      event_id: "ev-1",
      fields: ["event.name"],
    });
  });
});

describe("ExportPage — participation status metadata (backend-authoritative)", () => {
  async function openEventFilters(user: User) {
    await user.click(screen.getByRole("radio", { name: /Участники события/ }));
    await next(user);
    await user.click(await screen.findByRole("checkbox", { name: "Мероприятие" }));
    await next(user);
  }

  it("loads the participation statuses from the backend and shows exactly those options", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await openEventFilters(user);

    const select = await screen.findByLabelText("Статус участия в событии");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Любой статус",
      "Записан (backend)",
      "Статус только с backend",
    ]);
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value),
    ).toEqual(["", "registered", "backend_only_status"]);
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships/exports/filters")),
    ).toBe(true);
  });

  it("does not load participation metadata for a dataset without an Event", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);
    await user.click(await screen.findByRole("checkbox", { name: "Фамилия" }));
    await next(user);

    expect(screen.queryByLabelText("Статус участия в событии")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships/exports/filters")),
    ).toBe(false);
  });

  it("sends the selected backend value and shows its backend label in the summary", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await openEventFilters(user);
    await user.selectOptions(await screen.findByLabelText("Статус участия в событии"), "registered");
    await user.click(await screen.findByRole("button", { name: /Поход на Эльбрус/ }));
    await next(user);
    await next(user);

    expect(
      within(screen.getByLabelText("Параметры экспорта")).getByText("Записан (backend)"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).toMatchObject({
      context: "event",
      event_id: "ev-1",
      participation_status: "registered",
    });
  });

  it("omits participation_status when no status is chosen", async () => {
    const fetchMock = exportBackend();
    renderExport();
    const user = userEvent.setup();

    await openEventFilters(user);
    await screen.findByLabelText("Статус участия в событии");
    await user.click(await screen.findByRole("button", { name: /Поход на Эльбрус/ }));
    await next(user);
    await next(user);
    await user.click(screen.getByRole("button", { name: "Экспортировать" }));

    await waitFor(() => expect(exportRequestBody(fetchMock)).toBeDefined());
    expect(exportRequestBody(fetchMock)).not.toHaveProperty("participation_status");
  });

  it("shows an error state on metadata failure, offers no fallback values and blocks the step", async () => {
    exportBackend([
      {
        method: "GET",
        match: "/memberships/exports/filters",
        status: 500,
        body: { error: { code: "internal_error", message: "Сбой сервера" } },
      },
    ]);
    renderExport();
    const user = userEvent.setup();

    await openEventFilters(user);
    await user.click(await screen.findByRole("button", { name: /Поход на Эльбрус/ }));

    const alert = await screen.findByText(/Не удалось загрузить статусы участия/);
    expect(alert).toHaveTextContent("Сбой сервера");
    // No select and no invented list of statuses.
    expect(screen.queryByLabelText("Статус участия в событии")).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Зарегистрирован|отменена/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/registered|cancelled/)).not.toBeInTheDocument();
    // An Event export cannot continue without the backend vocabulary.
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
  });

  it("keeps no hardcoded participation status values in the wizard source", () => {
    expect(exportPageSource).not.toMatch(/["'`](registered|cancelled)["'`]/);
  });
});
