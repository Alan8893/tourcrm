import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { ImportPage } from "./ImportPage";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi, requests, type MockApiHandler } from "../test/mockApi";
import type { ImportJob, ImportJobIssue, ImportJobStatus, ImportJobStatistics } from "../api/imports";

const NULL_STATS: ImportJobStatistics = {
  total_records: null,
  valid_records: null,
  invalid_records: null,
  created_records: null,
  updated_records: null,
  skipped_records: null,
  error_count: 0,
};

function job(status: ImportJobStatus, statistics: Partial<ImportJobStatistics> = {}): ImportJob {
  return {
    import_id: "imp-1",
    club_id: "club-1",
    created_by_user_id: "u1",
    status,
    format: "csv",
    statistics: { ...NULL_STATS, ...statistics },
    created_at: "2026-09-30T10:00:00Z",
    updated_at: "2026-09-30T10:00:00Z",
  };
}

function collection<T>(items: T[]) {
  return { items, pagination: { page: 1, page_size: 50, total: items.length, pages: items.length ? 1 : 0 } };
}

const PREVIEW_STATS = { total_records: 5, valid_records: 3, invalid_records: 2, error_count: 2 };

const ROW_ERRORS: ImportJobIssue[] = [
  {
    id: "e1",
    row_number: 3,
    field: "email",
    code: "invalid_email",
    message: "Invalid email",
    severity: "error",
    matched_person_id: null,
    created_at: "2026-09-30T10:00:00Z",
  },
  {
    id: "e2",
    row_number: 4,
    field: "first_name",
    code: "required_field_missing",
    message: "Required",
    severity: "error",
    matched_person_id: null,
    created_at: "2026-09-30T10:00:00Z",
  },
];

const ROW_WARNINGS: ImportJobIssue[] = [
  {
    id: "w1",
    row_number: 5,
    field: null,
    code: "duplicate_exact",
    message: "Duplicate",
    severity: "warning",
    matched_person_id: "person-42",
    created_at: "2026-09-30T10:00:00Z",
  },
  {
    id: "w2",
    row_number: 6,
    field: null,
    code: "duplicate_exact",
    message: "Duplicate",
    severity: "warning",
    matched_person_id: null,
    created_at: "2026-09-30T10:00:00Z",
  },
];

/** A stateful fake of the six import endpoints: every transition only
 * happens when its own POST arrives. */
function importBackend(
  options: {
    previewFails?: boolean;
  } = {},
) {
  let current: ImportJob | null = null;
  const fileLevelError: ImportJobIssue = {
    id: "f1",
    row_number: null,
    field: null,
    code: "import_header_missing_required_column",
    message: "Missing required column",
    severity: "error",
    matched_person_id: null,
    created_at: "2026-09-30T10:00:00Z",
  };
  const handlers: MockApiHandler[] = [
    {
      method: "POST",
      match: "/memberships/imports/imp-1/preview",
      status: options.previewFails ? 422 : 200,
      body: () => {
        if (options.previewFails) {
          current = job("failed", { error_count: 1 });
          return {
            error: {
              code: "import_validation_failed",
              message: "The import file could not be parsed or validated",
            },
          };
        }
        current = job("preview_ready", PREVIEW_STATS);
        return current;
      },
    },
    {
      method: "POST",
      match: "/memberships/imports/imp-1/approve",
      body: () => {
        current = job("approved", PREVIEW_STATS);
        return current;
      },
    },
    {
      method: "POST",
      match: "/memberships/imports/imp-1/apply",
      body: () => {
        current = job("completed", {
          ...PREVIEW_STATS,
          created_records: 2,
          updated_records: 0,
          skipped_records: 3,
        });
        return current;
      },
    },
    {
      method: "GET",
      match: "severity=error",
      body: () =>
        collection(current?.status === "failed" ? [fileLevelError] : ROW_ERRORS),
    },
    {
      method: "GET",
      match: "severity=warning",
      body: () => collection(current?.status === "failed" ? [] : ROW_WARNINGS),
    },
    {
      method: "GET",
      match: "/memberships/imports/imp-1",
      status: 200,
      body: () => current,
    },
    {
      method: "POST",
      match: "/memberships/imports",
      status: 201,
      body: () => {
        current = job("uploaded");
        return {
          import_id: "imp-1",
          status: "uploaded",
          format: "csv",
          created_at: "2026-09-30T10:00:00Z",
        };
      },
    },
  ];
  return mockApi(handlers);
}

function renderImport(route = "/people/import") {
  return renderWithProviders(
    <Routes>
      <Route path="/people/import" element={<ImportPage />} />
      <Route path="/people" element={<h1>Список людей</h1>} />
    </Routes>,
    { route },
  );
}

function postsTo(fetchMock: ReturnType<typeof mockApi>, fragment: string) {
  return requests(fetchMock).filter(([method, url]) => method === "POST" && url.includes(fragment));
}

async function uploadFile(user: ReturnType<typeof userEvent.setup>, name = "people.csv") {
  const file = new File(["first_name,last_name\nАнна,Иванова\n"], name, { type: "text/csv" });
  await user.upload(await screen.findByLabelText("Файл для импорта"), file);
  await user.click(screen.getByRole("button", { name: "Загрузить файл" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ImportPage — canonical workflow upload → preview → approve → apply → report", () => {
  it("uploads the file without parsing or applying it", async () => {
    const fetchMock = importBackend();
    renderImport();
    const user = userEvent.setup();

    expect(screen.getByRole("heading", { name: "Импорт участников" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Загрузить файл" })).toBeDisabled();
    // The MVP creates only Person + User + ClubMembership: no group,
    // role, instructor or guardian selection is offered.
    expect(screen.queryByLabelText(/Группа/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Роль/)).not.toBeInTheDocument();

    await uploadFile(user);

    expect(await screen.findByRole("heading", { name: "Файл загружен" })).toBeInTheDocument();
    const [upload] = postsTo(fetchMock, "/memberships/imports");
    expect(upload).toBeDefined();
    const uploadInit = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")?.[1];
    expect(uploadInit?.body).toBeInstanceOf(FormData);
    expect(postsTo(fetchMock, "/preview")).toHaveLength(0);
    expect(postsTo(fetchMock, "/approve")).toHaveLength(0);
    expect(postsTo(fetchMock, "/apply")).toHaveLength(0);
  });

  it("walks through validation, preview, approval, confirmed apply and the report", async () => {
    const fetchMock = importBackend();
    renderImport();
    const user = userEvent.setup();

    await uploadFile(user);
    await user.click(await screen.findByRole("button", { name: "Проверить файл" }));

    // Preview: backend counters, blocking errors and duplicate warnings.
    expect(await screen.findByRole("heading", { name: "Предпросмотр" })).toBeInTheDocument();
    const stats = screen.getByLabelText("Итоги проверки");
    expect(within(stats).getByText("Всего строк").nextSibling).toHaveTextContent("5");
    expect(within(stats).getByText("Без ошибок").nextSibling).toHaveTextContent("3");
    expect(within(stats).getByText("С ошибками (будут пропущены)").nextSibling).toHaveTextContent("2");
    await waitFor(() =>
      expect(within(stats).getByText("Предупреждения о дубликатах").nextSibling).toHaveTextContent("2"),
    );

    const errors = await screen.findByRole("region", { name: /Блокирующие ошибки/ });
    expect(within(errors).getByText("Некорректный email")).toBeInTheDocument();
    expect(within(errors).getByText("Строка 3 · колонка email")).toBeInTheDocument();
    expect(within(errors).getByText("Не заполнено обязательное поле")).toBeInTheDocument();

    const warnings = screen.getByRole("region", { name: /Предупреждения и возможные дубликаты/ });
    expect(within(warnings).getAllByText("Возможный дубликат — строка будет пропущена")).toHaveLength(2);
    expect(within(warnings).getByRole("link", { name: "Открыть совпавшего человека" })).toHaveAttribute(
      "href",
      "/people/person-42",
    );
    expect(within(warnings).getByText("Совпадает с другой строкой файла")).toBeInTheDocument();
    expect(screen.getByText(/Группы, роли, инструкторы и представители импортом не назначаются/)).toBeInTheDocument();
    expect(postsTo(fetchMock, "/approve")).toHaveLength(0);

    // Approval requires an explicit acknowledgement.
    await user.click(screen.getByRole("button", { name: "Перейти к подтверждению" }));
    const approveButton = await screen.findByRole("button", { name: "Одобрить импорт" });
    expect(approveButton).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /Я проверил\(а\) предпросмотр/ }));
    await user.click(approveButton);

    expect(await screen.findByRole("heading", { name: "Импорт одобрен" })).toBeInTheDocument();
    expect(postsTo(fetchMock, "/approve")).toHaveLength(1);
    expect(postsTo(fetchMock, "/apply")).toHaveLength(0);

    // Apply needs a confirmation dialog; cancelling never applies.
    await user.click(screen.getByRole("button", { name: "Применить импорт" }));
    let dialog = await screen.findByRole("dialog", { name: "Применить импорт?" });
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(postsTo(fetchMock, "/apply")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Применить импорт" }));
    dialog = await screen.findByRole("dialog", { name: "Применить импорт?" });
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));

    // Report.
    expect(await screen.findByRole("heading", { name: "Результат" })).toBeInTheDocument();
    expect(postsTo(fetchMock, "/apply")).toHaveLength(1);
    // Status badge in the report plus the success toast.
    expect(screen.getAllByText("Импорт завершён")).toHaveLength(2);
    const report = screen.getByLabelText("Итоги импорта");
    expect(within(report).getByText("Создано").nextSibling).toHaveTextContent("2");
    expect(within(report).getByText("Пропущено").nextSibling).toHaveTextContent("3");
    expect(screen.getByText(/Исходный файл удалён после успешного импорта/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Новый импорт" })).toBeInTheDocument();
  });

  it("shows the failed job and its file-level errors when the file cannot be validated", async () => {
    importBackend({ previewFails: true });
    renderImport();
    const user = userEvent.setup();

    await uploadFile(user);
    await user.click(await screen.findByRole("button", { name: "Проверить файл" }));

    expect(await screen.findByText("Импорт не выполнен", { selector: "span" })).toBeInTheDocument();
    const errors = await screen.findByRole("region", { name: /Ошибки/ });
    expect(await within(errors).findByText("Нет обязательной колонки")).toBeInTheDocument();
    expect(within(errors).getByText("Весь файл")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Одобрить импорт" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Применить импорт" })).not.toBeInTheDocument();
  });

  it("shows the backend rejection of an unsupported file format", async () => {
    mockApi([
      {
        method: "POST",
        match: "/memberships/imports",
        status: 422,
        body: { error: { code: "unsupported_import_format", message: "Import file must be CSV or XLSX" } },
      },
    ]);
    renderImport();
    const user = userEvent.setup({ applyAccept: false });

    await uploadFile(user, "people.txt");

    expect(await screen.findByRole("alert")).toHaveTextContent("Поддерживаются только файлы CSV и XLSX.");
  });

  it("resumes a job from the URL and reports a partially completed apply", async () => {
    const applyFailed: ImportJobIssue = {
      id: "a1",
      row_number: 7,
      field: null,
      code: "import_apply_failed",
      message: "Row failed",
      severity: "error",
      matched_person_id: null,
      created_at: "2026-09-30T10:00:00Z",
    };
    mockApi([
      { method: "GET", match: "severity=error", body: collection([applyFailed]) },
      { method: "GET", match: "severity=warning", body: collection([]) },
      {
        method: "GET",
        match: "/memberships/imports/imp-1",
        body: job("partially_completed", {
          ...PREVIEW_STATS,
          created_records: 2,
          updated_records: 0,
          skipped_records: 2,
          error_count: 3,
        }),
      },
    ]);
    renderImport("/people/import?job=imp-1");

    expect(await screen.findByText("Импорт завершён частично", { selector: "span" })).toBeInTheDocument();
    expect(await screen.findByText("Строку не удалось импортировать")).toBeInTheDocument();
    expect(screen.getByText("Строка 7")).toBeInTheDocument();
    expect(screen.getByText(/Исходный файл сохранён/)).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown or hidden job", async () => {
    mockApi([
      {
        method: "GET",
        match: "/memberships/imports/imp-404",
        status: 404,
        body: { error: { code: "not_found", message: "Import job not found" } },
      },
    ]);
    renderImport("/people/import?job=imp-404");

    expect(await screen.findByRole("heading", { name: "Импорт не найден" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Начать новый импорт" })).toBeInTheDocument();
  });
});

describe("ImportPage — downloadable import templates (Issue #228)", () => {
  /** The file a download link points to, read from `public/` (served at
   * the site root by Vite and in the production build). */
  async function publicFile(link: HTMLElement): Promise<Buffer> {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    return readFileSync(join(process.cwd(), "public", link.getAttribute("href") ?? ""));
  }

  function templateLinks() {
    return {
      csv: screen.getByRole("link", { name: "Скачать пример CSV" }),
      xlsx: screen.getByRole("link", { name: "Скачать пример XLSX" }),
    };
  }

  /** The column names the page itself documents next to the downloads. */
  function documentedColumns(): string[] {
    const description = screen.getByText(/Обязательные колонки:/);
    return Array.from(description.querySelectorAll("span")).map((span) => span.textContent ?? "");
  }

  it("offers both templates on the upload step, next to the column description, as static downloads", async () => {
    const fetchMock = importBackend();
    renderImport();
    await screen.findByRole("heading", { name: "Выберите файл" });

    const { csv, xlsx } = templateLinks();
    expect(csv).toHaveAttribute("href", "/templates/participant-import-template.csv");
    expect(xlsx).toHaveAttribute("href", "/templates/participant-import-template.xlsx");
    expect(csv).toHaveAttribute("download");
    expect(xlsx).toHaveAttribute("download");
    // Same card as the accepted-format/column explanation.
    const card = screen.getByRole("heading", { name: "Выберите файл" }).parentElement as HTMLElement;
    expect(within(card).getByText(/Обязательные колонки:/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Скачать пример CSV" })).toBe(csv);
    // A template is a static asset: no API request, no import job.
    expect(requests(fetchMock)).toHaveLength(0);
  });

  it("the CSV template holds exactly the documented columns, in order, and no participant rows", async () => {
    importBackend();
    renderImport();
    await screen.findByRole("heading", { name: "Выберите файл" });
    const bytes = await publicFile(templateLinks().csv);
    const text = new TextDecoder("utf-8", { ignoreBOM: false }).decode(bytes);
    const lines = text.split(/\r?\n/).filter((line) => line !== "");
    expect(lines).toHaveLength(1);
    expect(lines[0]!.split(",")).toEqual(documentedColumns());
  });

  it("the XLSX template is a real workbook file", async () => {
    importBackend();
    renderImport();
    await screen.findByRole("heading", { name: "Выберите файл" });
    const bytes = await publicFile(templateLinks().xlsx);
    // XLSX is a ZIP container (local file header "PK\x03\x04") holding the
    // workbook part; its header row is checked against the backend contract
    // by apps/api tests/unit/test_import_templates.py.
    expect(Array.from(bytes.subarray(0, 4))).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(bytes.includes(Buffer.from("xl/workbook.xml"))).toBe(true);
  });
});
