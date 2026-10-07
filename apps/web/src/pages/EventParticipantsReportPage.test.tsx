import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { EventParticipantsReportPage } from "./EventParticipantsReportPage";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi, type MockApiHandler } from "../test/mockApi";
import { printHtmlBlob, saveBlob } from "../api/client";
import reportPageSource from "./EventParticipantsReportPage.tsx?raw";
import exportStepsSource from "./ParticipantExportSteps.tsx?raw";
import exportDomainSource from "../domain/participantExport.ts?raw";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, saveBlob: vi.fn(), printHtmlBlob: vi.fn() };
});

/**
 * Issue #299 — «Отчёты → Участники мероприятий»: context → filters →
 * preview → XLSX/PDF/Print over the one canonical Participant Export
 * dataset. The backend fixtures below stand in for the real endpoints;
 * every assertion is about what the page sends and renders.
 */

const ALL = ["club", "group", "event", "group_event"];

/** `GET /memberships/exports/fields` — the backend allowlist. */
const FIELDS = {
  items: [
    { field_code: "person.last_name", label: "Фамилия", contexts: ALL },
    { field_code: "person.first_name", label: "Имя", contexts: ALL },
    { field_code: "person.middle_name", label: "Отчество", contexts: ALL },
    { field_code: "person.birth_date", label: "Дата рождения", contexts: ALL },
    { field_code: "person.phone", label: "Телефон", contexts: ALL },
    { field_code: "group.name", label: "Группа", contexts: ["group", "group_event"] },
    { field_code: "membership.status", label: "Статус членства", contexts: ALL },
    { field_code: "event.name", label: "Мероприятие", contexts: ["event", "group_event"] },
    { field_code: "event.starts_at", label: "Начало мероприятия", contexts: ["event", "group_event"] },
    { field_code: "event_participation.status", label: "Статус участия", contexts: ["event", "group_event"] },
  ],
};

/** `GET /memberships/exports/filters` — labels only the backend knows. */
const FILTERS = {
  participation_status: [
    { value: "registered", label: "Зарегистрирован (backend)" },
    { value: "cancelled", label: "Регистрация отменена (backend)" },
  ],
};

const GROUPS = {
  items: [
    {
      id: "g1",
      club_id: "club-1",
      name: "Юные туристы",
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
      title: "Поход выходного дня",
      description: null,
      start_at: "2026-11-14T07:00:00Z",
      end_at: "2026-11-14T15:00:00Z",
      timezone: "Europe/Moscow",
      status: "published",
    },
  ],
  pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
};

const REPORT_COLUMNS = [
  { field_code: "person.last_name", label: "Фамилия" },
  { field_code: "person.first_name", label: "Имя" },
  { field_code: "person.middle_name", label: "Отчество" },
  { field_code: "group.name", label: "Группа" },
  { field_code: "event.name", label: "Мероприятие" },
  { field_code: "event.starts_at", label: "Начало мероприятия" },
  { field_code: "event_participation.status", label: "Статус участия" },
];

function previewBody(
  rows: string[][],
  pagination: { page?: number; total?: number; pages?: number } = {},
  columns = REPORT_COLUMNS,
) {
  const total = pagination.total ?? rows.length;
  return {
    title: "Участники группы «Юные туристы» — мероприятие «Поход выходного дня»",
    columns,
    items: rows,
    pagination: {
      page: pagination.page ?? 1,
      page_size: 50,
      total,
      pages: pagination.pages ?? (total ? 1 : 0),
    },
  };
}

const CANCELLED_ROW = [
  "Иванова",
  "Мария",
  "",
  "Юные туристы",
  "Поход выходного дня",
  "2026-11-14 10:00",
  "cancelled",
];

function reportBackend(preview: Partial<MockApiHandler> = {}, extra: MockApiHandler[] = []) {
  // The preview handler goes first: "/memberships/exports" also matches it.
  return mockApi([
    { method: "POST", match: "/memberships/exports/preview", body: previewBody([CANCELLED_ROW]), ...preview },
    ...extra,
    { method: "POST", match: "/memberships/exports", body: {} },
    { method: "GET", match: "/memberships/exports/fields", body: FIELDS },
    { method: "GET", match: "/memberships/exports/filters", body: FILTERS },
    { method: "GET", match: "/groups", body: GROUPS },
    { method: "GET", match: "/events?", body: EVENTS },
  ]);
}

function bodiesOf(fetchMock: ReturnType<typeof mockApi>, suffix: string) {
  return fetchMock.mock.calls
    .filter(([input, init]) => String(input).endsWith(suffix) && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init?.body)));
}

function renderReport() {
  return renderWithProviders(
    <Routes>
      <Route path="/reports/event-participants" element={<EventParticipantsReportPage />} />
    </Routes>,
    { route: "/reports/event-participants" },
  );
}

type User = ReturnType<typeof userEvent.setup>;

async function next(user: User) {
  await user.click(screen.getByRole("button", { name: "Далее" }));
}

/** Group + Event context, Group «Юные туристы», Event «Поход выходного
 * дня», optional backend participation status label. */
async function configureGroupEvent(user: User, statusLabel?: string) {
  await user.click(screen.getByRole("radio", { name: /Группа на событии/ }));
  await next(user);
  await user.selectOptions(await screen.findByLabelText("Группа"), "g1");
  await user.click(await screen.findByRole("button", { name: /Поход выходного дня/ }));
  const statusSelect = await screen.findByLabelText("Статус участия в событии");
  if (statusLabel) {
    await user.selectOptions(statusSelect, screen.getByRole("option", { name: statusLabel }));
  }
  await next(user);
}

beforeEach(() => {
  vi.mocked(saveBlob).mockClear();
  vi.mocked(printHtmlBlob).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("«Участники мероприятий» — wizard", () => {
  it("starts with the context step and offers the four canonical contexts", async () => {
    reportBackend();
    renderReport();

    expect(screen.getByRole("heading", { name: "Участники мероприятий" })).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Шаги отчёта" });
    expect(within(steps).getAllByRole("listitem").map((step) => step.textContent)).toEqual([
      "1Контекст",
      "2Фильтры",
      "3Просмотр и выгрузка",
    ]);
    expect(screen.getAllByRole("radio").map((radio) => (radio as HTMLInputElement).value)).toEqual([
      "club",
      "group",
      "event",
      "group_event",
    ]);
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
  });

  it("requires the Group and Event of a Group + Event report before the preview", async () => {
    reportBackend();
    renderReport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Группа на событии/ }));
    await next(user);
    await screen.findByLabelText("Статус участия в событии");
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.selectOptions(await screen.findByLabelText("Группа"), "g1");
    expect(screen.getByRole("button", { name: "Далее" })).toBeDisabled();
    await user.click(await screen.findByRole("button", { name: /Поход выходного дня/ }));
    expect(screen.getByRole("button", { name: "Далее" })).toBeEnabled();
  });

  it("offers exactly the backend participation statuses — no frontend list", async () => {
    reportBackend();
    renderReport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Участники события/ }));
    await next(user);
    const select = await screen.findByLabelText("Статус участия в событии");
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => [(option as HTMLOptionElement).value, option.textContent]),
    ).toEqual([
      ["", "Любой статус"],
      ["registered", "Зарегистрирован (backend)"],
      ["cancelled", "Регистрация отменена (backend)"],
    ]);
  });

  it("keeps no hardcoded participation status values in the report sources", () => {
    for (const source of [reportPageSource, exportStepsSource, exportDomainSource]) {
      expect(source).not.toMatch(/["'`](registered|cancelled)["'`]/);
    }
  });
});

describe("«Участники мероприятий» — preview", () => {
  it("previews one backend page of the selection with the minimum report columns", async () => {
    const fetchMock = reportBackend();
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user, "Регистрация отменена (backend)");

    const preview = await screen.findByRole("region", { name: "Предпросмотр отчёта" });
    expect(within(preview).getByRole("heading", { name: /Юные туристы.*Поход выходного дня/ })).toBeInTheDocument();
    expect(within(preview).getByText("Найдено: 1")).toBeInTheDocument();
    expect(within(preview).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
      "Фамилия",
      "Имя",
      "Отчество",
      "Группа",
      "Мероприятие",
      "Начало мероприятия",
      "Статус участия",
    ]);
    const [, row] = within(preview).getAllByRole("row");
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(CANCELLED_ROW);

    expect(bodiesOf(fetchMock, "/memberships/exports/preview")).toEqual([
      {
        context: "group_event",
        group_id: "g1",
        event_id: "ev-1",
        membership_status: "active",
        participation_status: "cancelled",
        fields: REPORT_COLUMNS.map((column) => column.field_code),
        page: 1,
        page_size: 50,
      },
    ]);
  });

  it("omits participation_status for «Любой статус» — the backend returns every status", async () => {
    const fetchMock = reportBackend();
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user);
    await screen.findByRole("region", { name: "Предпросмотр отчёта" });

    const [body] = bodiesOf(fetchMock, "/memberships/exports/preview");
    expect(body).not.toHaveProperty("participation_status");
  });

  it("sends only the minimum columns the backend offers for a club report, and no Event filters", async () => {
    const fetchMock = reportBackend({ body: previewBody([["Иванова", "Мария", ""]], {}, REPORT_COLUMNS.slice(0, 3)) });
    renderReport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);
    expect(screen.queryByLabelText("Статус участия в событии")).not.toBeInTheDocument();
    await next(user);
    await screen.findByText("Найдено: 1");

    expect(bodiesOf(fetchMock, "/memberships/exports/preview")).toEqual([
      {
        context: "club",
        membership_status: "active",
        fields: ["person.last_name", "person.first_name", "person.middle_name"],
        page: 1,
        page_size: 50,
      },
    ]);
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships/exports/filters")),
    ).toBe(false);
  });

  it("adds an optional allowlist field to the preview on request", async () => {
    const fetchMock = reportBackend();
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user);
    await screen.findByText("Найдено: 1");
    await user.click(screen.getByText("Дополнительные поля"));
    const extras = screen.getByRole("group", { name: "Добавить в отчёт" });
    expect(within(extras).getAllByRole("checkbox").map((box) => box.closest("label")?.textContent)).toEqual([
      "Дата рождения",
      "Телефон",
      "Статус членства",
    ]);
    await user.click(within(extras).getByRole("checkbox", { name: "Телефон" }));

    await waitFor(() => expect(bodiesOf(fetchMock, "/memberships/exports/preview")).toHaveLength(2));
    expect(bodiesOf(fetchMock, "/memberships/exports/preview")[1].fields).toEqual([
      "person.last_name",
      "person.first_name",
      "person.middle_name",
      "person.phone",
      "group.name",
      "event.name",
      "event.starts_at",
      "event_participation.status",
    ]);
  });

  it("pages through the preview with the backend pagination", async () => {
    let requestedPage = 0;
    const fetchMock = reportBackend({
      body: (init: RequestInit | undefined) => {
        requestedPage = JSON.parse(String(init?.body)).page;
        return previewBody([[...CANCELLED_ROW.slice(0, 6), `page-${requestedPage}`]], {
          page: requestedPage,
          total: 51,
          pages: 2,
        });
      },
    });
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user);
    expect(await screen.findByText("Страница 1 из 2 · всего 51")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByText("page-2")).toBeInTheDocument();
    expect(screen.getByText("Страница 2 из 2 · всего 51")).toBeInTheDocument();
    expect(bodiesOf(fetchMock, "/memberships/exports/preview").map((body) => body.page)).toEqual([1, 2]);
  });

  it("shows an empty state when the dataset has no rows", async () => {
    reportBackend({ body: previewBody([]) });
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user, "Регистрация отменена (backend)");

    expect(await screen.findByText("Участники не найдены")).toBeInTheDocument();
    expect(screen.getByText("Найдено: 0")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the backend's existence-hiding 404 for the preview without a retry", async () => {
    reportBackend({
      status: 404,
      body: { error: { code: "not_found", message: "Event not found", details: {}, request_id: "r" } },
    });
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user);

    expect(await screen.findByText("Не удалось сформировать предпросмотр")).toBeInTheDocument();
    expect(screen.getByText("Выбранная группа или событие не найдены.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });
});

describe("«Участники мероприятий» — XLSX / PDF / Print from the same dataset", () => {
  it.each([
    ["XLSX", "xlsx"],
    ["PDF", "pdf"],
    ["Печать", "print"],
  ])("%s sends the preview's own selection to the one export endpoint", async (label, format) => {
    const fetchMock = reportBackend();
    renderReport();
    const user = userEvent.setup();

    await configureGroupEvent(user, "Регистрация отменена (backend)");
    await screen.findByText("Найдено: 1");
    await user.click(
      within(screen.getByRole("group", { name: "Выгрузка отчёта" })).getByRole("button", { name: label }),
    );

    await waitFor(() => expect(bodiesOf(fetchMock, "/memberships/exports")).toHaveLength(1));
    const [previewRequest] = bodiesOf(fetchMock, "/memberships/exports/preview");
    const [exportRequest] = bodiesOf(fetchMock, "/memberships/exports");
    const selection = Object.fromEntries(
      Object.entries(previewRequest).filter(([key]) => key !== "page" && key !== "page_size"),
    );
    expect(exportRequest).toEqual({ ...selection, format });
    if (format === "print") {
      await waitFor(() => expect(printHtmlBlob).toHaveBeenCalledTimes(1));
      expect(saveBlob).not.toHaveBeenCalled();
    } else {
      await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1));
    }
    expect(await screen.findByText("Отчёт сформирован")).toBeInTheDocument();
  });

  it("shows the backend 403 of the export without saving anything", async () => {
    reportBackend({}, [
      {
        method: "POST",
        match: "/memberships/exports",
        status: 403,
        body: { error: { code: "forbidden", message: "Forbidden", details: {}, request_id: "r" } },
      },
    ]);
    renderReport();
    const user = userEvent.setup();

    await user.click(screen.getByRole("radio", { name: /Все участники/ }));
    await next(user);
    await next(user);
    await user.click(
      within(screen.getByRole("group", { name: "Выгрузка отчёта" })).getByRole("button", { name: "XLSX" }),
    );

    expect(await screen.findByText("У вас нет прав на этот экспорт.")).toBeInTheDocument();
    // The preview itself was served; only the export was refused.
    expect(screen.getByText("Найдено: 1")).toBeInTheDocument();
    expect(saveBlob).not.toHaveBeenCalled();
  });
});

describe("«Участники мероприятий» — access", () => {
  it("renders the forbidden state when the backend denies the export metadata", async () => {
    const fetchMock = mockApi([
      {
        method: "GET",
        match: "/memberships/exports/fields",
        status: 403,
        body: { error: { code: "forbidden", message: "Forbidden", details: {}, request_id: "r" } },
      },
    ]);
    renderReport();

    expect(await screen.findByText("Отчёт недоступен")).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/memberships/exports/preview")),
    ).toBe(false);
  });
});
