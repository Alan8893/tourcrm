import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Inventory operations (Issue #238): the Administrator's warehouse
 * workflows rendered through the real App and API client, with `fetch`
 * replaced by a small in-memory fake of the documented endpoints
 * (docs/05-api/endpoint-inventory.md §19). The fake only answers what a
 * test sets up; the UI is checked for the requests it sends and for the
 * refetched backend data it shows afterwards.
 */

type Json = Record<string, unknown>;
type Request = { method: string; path: string; search: URLSearchParams; body: Json | null };
type Reply = { status?: number; body?: unknown } | "network-error";
type Route = (request: Request) => Reply | undefined;

function page<T>(items: T[]) {
  return { items, pagination: { page: 1, page_size: 20, total: items.length, pages: items.length ? 1 : 0 } };
}

function apiError(status: number, code: string, message = "Backend message") {
  return { status, body: { error: { code, message, details: {}, request_id: "r1" } } };
}

const ME = {
  user: {
    id: "u1",
    login_identifier: "admin@example.com",
    status: "active",
    email_verified_at: null,
    person: { id: "p1", first_name: "Анна", last_name: "Иванова", middle_name: null, birth_date: null, photo_file_id: null },
  },
  role_assignments: [{ role_code: "admin", club_id: "club-1", scope_type: "all" }],
};

const CATEGORY = { id: "c1", name: "Страховочное снаряжение", status: "active" };
const UNITS = [
  { id: "un1", name: "шт", is_system: true, status: "active" },
  { id: "un2", name: "м", is_system: true, status: "active" },
  { id: "un3", name: "рулон", is_system: false, status: "active" },
];
const LOCATIONS = [
  { id: "l1", parent_id: null, name: "Склад клуба", status: "active" },
  { id: "l2", parent_id: "l1", name: "Стеллаж А", status: "active" },
  { id: "l3", parent_id: "l2", name: "Полка 1", status: "active" },
];
const ROPE = {
  id: "i-rope",
  name: "Верёвка 10 мм",
  category_id: "c1",
  unit_id: "un2",
  accounting_mode: "quantity",
  current_cost_minor: 12050,
  status: "active",
  archived_at: null,
};
const JUMAR = {
  id: "i-jumar",
  name: "Жумар",
  category_id: "c1",
  unit_id: "un1",
  accounting_mode: "instance",
  current_cost_minor: null,
  status: "active",
  archived_at: null,
};

function instance(overrides: Json) {
  return {
    id: "inst-1",
    item_id: "i-jumar",
    inventory_number: "INV-000001",
    manufacturer_barcode: null,
    manufacturer_serial_number: null,
    description: null,
    state: "available",
    storage_location_id: "l2",
    ...overrides,
  };
}

function movement(overrides: Json) {
  return {
    id: "m1",
    item_id: "i-rope",
    instance_id: null,
    movement_type: "receipt",
    from_location_id: null,
    to_location_id: null,
    quantity: null,
    unit_cost_minor: null,
    comment: null,
    reverses_movement_id: null,
    issue_line_id: null,
    created_by: "u1",
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

function line(overrides: Json) {
  return {
    id: "ln-rope",
    issue_id: "iss-1",
    item_id: "i-rope",
    accounting_mode: "quantity",
    issued_quantity: 10,
    returned_quantity: 0,
    outstanding_quantity: 10,
    outstanding_instance_ids: [],
    created_by: "u1",
    created_at: "2026-10-01T09:00:00Z",
    removed_at: null,
    removed_by: null,
    ...overrides,
  };
}

type IssueState = Json & { lines: Json[]; removed: Json[] };

function issue(overrides: Partial<IssueState> = {}): IssueState {
  return {
    id: "iss-1",
    recipient_type: "member",
    recipient_id: "p-member",
    event_id: null,
    planned_return_date: "2026-10-10",
    comment: "На сборы",
    status: "issued",
    has_outstanding: true,
    cancelled_at: null,
    cancelled_by: null,
    created_by: "u1",
    created_at: "2026-10-01T09:00:00Z",
    updated_by: null,
    updated_at: "2026-10-01T09:00:00Z",
    lines: [line({})],
    removed: [],
    ...overrides,
  };
}

function issueDetail(state: IssueState) {
  const { removed: _removed, ...detail } = state;
  void _removed;
  return detail;
}

/** Reference data, lists and lookups every inventory screen reads;
 * `route` answers first. */
function baseRoute(route: Route, items: Json[] = [ROPE, JUMAR]): Route {
  return (request) => {
    const custom = route(request);
    if (custom) return custom;
    if (request.method !== "GET") return undefined;
    switch (request.path) {
      case "/api/v1/inventory/categories":
        return { body: page([CATEGORY]) };
      case "/api/v1/inventory/units":
        return { body: page(UNITS) };
      case "/api/v1/inventory/storage-locations":
        return { body: page(LOCATIONS) };
      case "/api/v1/inventory/items":
        return { body: page(items) };
      case "/api/v1/inventory/stock":
      case "/api/v1/inventory/instances":
      case "/api/v1/inventory/issues":
        return { body: page([]) };
      case "/api/v1/persons":
        return {
          body: page([
            { id: "p-member", first_name: "Пётр", last_name: "Сидоров", middle_name: null, role_codes: ["member"] },
          ]),
        };
      case "/api/v1/persons/p-member":
        return { body: { id: "p-member", first_name: "Пётр", last_name: "Сидоров", middle_name: null, role_codes: [] } };
      case "/api/v1/users":
        return { body: page([{ id: "u-instr", person_id: "p9", first_name: "Олег", last_name: "Инструкторов", middle_name: null }]) };
      case "/api/v1/groups":
        return { body: page([{ id: "g1", name: "Отряд «Север»", status: "active" }]) };
      case "/api/v1/groups/g1":
        return { body: { id: "g1", name: "Отряд «Север»", status: "active" } };
      case "/api/v1/events":
        return {
          body: page([{ id: "e1", title: "Поход на Эльбрус", start_at: "2026-10-05T06:00:00Z", status: "published" }]),
        };
      case "/api/v1/events/e1":
        return { body: { id: "e1", title: "Поход на Эльбрус", start_at: "2026-10-05T06:00:00Z" } };
      default:
        return undefined;
    }
  };
}

function stubApi(route: Route, me: Json = ME) {
  const calls: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Json) : null;
    const request = { method, path: url.pathname, search: url.searchParams, body };
    calls.push(request);
    const reply: Reply | undefined = url.pathname === "/api/v1/auth/me" ? { body: me } : route(request);
    if (!reply) throw new Error(`No stub for ${method} ${url.pathname}${url.search}`);
    if (reply === "network-error") throw new TypeError("Failed to fetch");
    return new Response(JSON.stringify(reply.body ?? {}), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function sent(calls: Request[], method: string, path: string): Request[] {
  return calls.filter((call) => call.method === method && call.path === path);
}

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  vi.resetModules();
  const { App } = await import("../App");
  render(<App />);
}

async function openDialog(buttonName: string | RegExp) {
  await userEvent.click(await screen.findByRole("button", { name: buttonName }));
  return screen.findByRole("dialog");
}

async function submit(dialog: HTMLElement, name: string | RegExp) {
  await userEvent.click(within(dialog).getByRole("button", { name }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

// --- nomenclature ----------------------------------------------------------------

describe("Inventory operations — nomenclature", () => {
  it("an empty nomenclature offers creation and the new item appears after the backend confirms it", async () => {
    const items: Json[] = [];
    const calls = stubApi(
      baseRoute((request) => {
        if (request.method === "POST" && request.path === "/api/v1/inventory/items") {
          const created = { ...ROPE, id: "i-new", ...request.body, status: "active", archived_at: null };
          items.push(created);
          return { status: 201, body: created };
        }
        if (request.method === "GET" && request.path === "/api/v1/inventory/items") return { body: page(items) };
        return undefined;
      }),
    );
    await renderAt("/inventory");

    const title = await screen.findByText("Номенклатуры пока нет");
    expect(title.parentElement?.querySelector("img")?.getAttribute("src")).toBe(
      "/assets/ui/illustrations/empty-inventory_128px.webp",
    );
    const emptyAction = within(title.parentElement as HTMLElement).getByRole("button", { name: "Создать номенклатуру" });
    await userEvent.click(emptyAction);
    const dialog = await screen.findByRole("dialog", { name: "Новая позиция" });
    await userEvent.type(within(dialog).getByLabelText("Наименование"), "Каска");
    await userEvent.selectOptions(within(dialog).getByLabelText("Категория"), "Страховочное снаряжение");
    await userEvent.selectOptions(within(dialog).getByLabelText("Единица измерения"), "шт");
    await userEvent.selectOptions(within(dialog).getByLabelText("Режим учёта"), "Поэкземплярный учёт");
    await userEvent.type(within(dialog).getByLabelText("Стоимость позиции, ₽"), "3 500,50");
    await submit(dialog, "Создать");

    expect(await screen.findByRole("link", { name: "Каска" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/items")[0]?.body).toEqual({
      name: "Каска",
      category_id: "c1",
      unit_id: "un1",
      accounting_mode: "instance",
      current_cost_minor: 350050,
    });
  });

  it("edit sends only the changed fields", async () => {
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "PATCH" && request.path === "/api/v1/inventory/items/i-rope"
          ? { body: { ...ROPE, current_cost_minor: 15000 } }
          : undefined,
      ),
    );
    await renderAt("/inventory");

    const card = (await screen.findByRole("link", { name: "Верёвка 10 мм" })).closest("li") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Изменить" }));
    const dialog = await screen.findByRole("dialog", { name: "Изменить позицию" });
    const cost = within(dialog).getByLabelText("Стоимость позиции, ₽");
    expect(cost).toHaveValue("120,50");
    await userEvent.clear(cost);
    await userEvent.type(cost, "150");
    await submit(dialog, "Сохранить");

    await waitFor(() => expect(sent(calls, "PATCH", "/api/v1/inventory/items/i-rope")).toHaveLength(1));
    expect(sent(calls, "PATCH", "/api/v1/inventory/items/i-rope")[0]?.body).toEqual({ current_cost_minor: 15000 });
  });

  it("shows the backend's accounting-mode lock instead of deciding it in the UI", async () => {
    stubApi(
      baseRoute((request) => {
        if (request.method === "PATCH") return apiError(409, "accounting_mode_locked", "Accounting mode is locked");
        if (request.path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (request.path.startsWith("/api/v1/inventory/items/i-rope/")) return { body: page([]) };
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    const dialog = await openDialog("Изменить");
    // The mode select stays enabled — the rule is the backend's.
    const mode = within(dialog).getByLabelText("Режим учёта");
    expect(mode).toBeEnabled();
    await userEvent.selectOptions(mode, "Поэкземплярный учёт");
    await submit(dialog, "Сохранить");

    expect(
      await within(dialog).findByText("Режим учёта нельзя изменить: по позиции уже есть движения или экземпляры."),
    ).toBeInTheDocument();
  });

  it("validates input before sending and shows a name conflict from the backend", async () => {
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "POST" && request.path === "/api/v1/inventory/items"
          ? apiError(409, "name_conflict")
          : undefined,
      ),
    );
    await renderAt("/inventory");

    const dialog = await openDialog("Создать номенклатуру");
    const create = within(dialog).getByRole("button", { name: "Создать" });
    expect(create).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Наименование"), "Верёвка 10 мм");
    await userEvent.selectOptions(within(dialog).getByLabelText("Категория"), "c1");
    await userEvent.selectOptions(within(dialog).getByLabelText("Единица измерения"), "un2");
    await userEvent.type(within(dialog).getByLabelText("Стоимость позиции, ₽"), "двести");
    expect(within(dialog).getByText("Введите сумму в рублях, например 1250 или 1250,50.")).toBeInTheDocument();
    expect(create).toBeDisabled();
    await userEvent.clear(within(dialog).getByLabelText("Стоимость позиции, ₽"));
    expect(create).toBeEnabled();
    await submit(dialog, "Создать");

    expect(await within(dialog).findByText("Запись с таким названием уже есть.")).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/items")).toHaveLength(1);
  });

  it("archives an item and reports the backend refusal when stock remains", async () => {
    let refuse = true;
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "POST" && request.path === "/api/v1/inventory/items/i-rope/archive"
          ? refuse
            ? apiError(409, "item_has_stock")
            : { body: { ...ROPE, status: "archived" } }
          : undefined,
      ),
    );
    await renderAt("/inventory");

    const card = (await screen.findByRole("link", { name: "Верёвка 10 мм" })).closest("li") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Архивировать" }));
    const dialog = await screen.findByRole("dialog", { name: "Архивировать позицию?" });
    await submit(dialog, "Архивировать");
    expect(await within(dialog).findByText("У позиции есть ненулевой остаток.")).toBeInTheDocument();

    refuse = false;
    await submit(dialog, "Архивировать");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent(calls, "POST", "/api/v1/inventory/items/i-rope/archive")).toHaveLength(2);
  });
});

// --- categories and units --------------------------------------------------------

describe("Inventory operations — categories and units", () => {
  it("creates a category and renames a custom unit; system units stay read-only", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.method === "POST" && request.path === "/api/v1/inventory/categories") {
          return { status: 201, body: { id: "c2", name: "Палатки", status: "active" } };
        }
        if (request.method === "PATCH" && request.path === "/api/v1/inventory/units/un3") {
          return { body: { ...UNITS[2], name: "бухта" } };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory?tab=references");

    const units = await screen.findByRole("list", { name: "Единицы измерения" });
    const system = within(units).getByText("шт").closest("li") as HTMLElement;
    expect(within(system).getByText("Системная единица — только чтение")).toBeInTheDocument();
    expect(within(system).queryByRole("button")).not.toBeInTheDocument();

    const category = await openDialog("Создать категорию");
    await userEvent.type(within(category).getByLabelText("Название"), "Палатки");
    await submit(category, "Создать");
    await waitFor(() => expect(sent(calls, "POST", "/api/v1/inventory/categories")).toHaveLength(1));
    expect(sent(calls, "POST", "/api/v1/inventory/categories")[0]?.body).toEqual({ name: "Палатки" });

    await userEvent.click(within(units).getByRole("button", { name: "Переименовать «рулон»" }));
    const unit = await screen.findByRole("dialog", { name: "Переименовать единицу" });
    await userEvent.clear(within(unit).getByLabelText("Название"));
    await userEvent.type(within(unit).getByLabelText("Название"), "бухта");
    await submit(unit, "Сохранить");
    await waitFor(() => expect(sent(calls, "PATCH", "/api/v1/inventory/units/un3")[0]?.body).toEqual({ name: "бухта" }));
  });
});

// --- storage locations -----------------------------------------------------------

describe("Inventory operations — storage locations", () => {
  it("creates a root location from the toolbar", async () => {
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "POST" && request.path === "/api/v1/inventory/storage-locations"
          ? { status: 201, body: { id: "l9", parent_id: null, name: "Гараж", status: "active" } }
          : undefined,
      ),
    );
    await renderAt("/inventory?tab=locations");

    const dialog = await openDialog("Создать место");
    await userEvent.type(within(dialog).getByLabelText("Название"), "Гараж");
    expect(within(dialog).getByLabelText("Входит в")).toHaveValue("");
    await submit(dialog, "Создать");

    await waitFor(() => expect(sent(calls, "POST", "/api/v1/inventory/storage-locations")).toHaveLength(1));
    expect(sent(calls, "POST", "/api/v1/inventory/storage-locations")[0]?.body).toEqual({
      name: "Гараж",
      parent_id: null,
    });
  });

  it("nests a location under any level of the hierarchy and shows full paths", async () => {
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "POST" && request.path === "/api/v1/inventory/storage-locations"
          ? { status: 201, body: { id: "l9", parent_id: "l3", name: "Ячейка 4", status: "active" } }
          : undefined,
      ),
    );
    await renderAt("/inventory?tab=locations");

    await userEvent.click(await screen.findByRole("button", { name: "Вложенное место в «Полка 1»" }));
    const dialog = await screen.findByRole("dialog", { name: "Новое место хранения" });
    const parent = within(dialog).getByLabelText("Входит в");
    expect(parent).toHaveValue("l3");
    expect(within(parent).getByRole("option", { name: "Склад клуба › Стеллаж А › Полка 1" })).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Название"), "Ячейка 4");
    await submit(dialog, "Создать");

    await waitFor(() =>
      expect(sent(calls, "POST", "/api/v1/inventory/storage-locations")[0]?.body).toEqual({
        name: "Ячейка 4",
        parent_id: "l3",
      }),
    );
  });

  it("edits a location (rename + move to root) and reports the backend's archive refusal", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.method === "PATCH") return { body: { ...LOCATIONS[1], name: "Стеллаж Б", parent_id: null } };
        if (request.method === "POST" && request.path.endsWith("/archive")) {
          return apiError(409, "location_has_active_children");
        }
        return undefined;
      }),
    );
    await renderAt("/inventory?tab=locations");

    await userEvent.click(await screen.findByRole("button", { name: "Изменить «Стеллаж А»" }));
    const edit = await screen.findByRole("dialog", { name: "Изменить место хранения" });
    // The location itself is never offered as its own parent.
    expect(within(edit).queryByRole("option", { name: "Склад клуба › Стеллаж А" })).not.toBeInTheDocument();
    await userEvent.clear(within(edit).getByLabelText("Название"));
    await userEvent.type(within(edit).getByLabelText("Название"), "Стеллаж Б");
    await userEvent.selectOptions(within(edit).getByLabelText("Входит в"), "");
    await submit(edit, "Сохранить");
    await waitFor(() =>
      expect(sent(calls, "PATCH", "/api/v1/inventory/storage-locations/l2")[0]?.body).toEqual({
        name: "Стеллаж Б",
        parent_id: null,
      }),
    );

    await userEvent.click(await screen.findByRole("button", { name: "Архивировать «Склад клуба»" }));
    const archive = await screen.findByRole("dialog", { name: "Архивировать место хранения?" });
    await submit(archive, "Архивировать");
    expect(
      await within(archive).findByText("Сначала архивируйте или перенесите вложенные места хранения."),
    ).toBeInTheDocument();
  });
});

// --- quantity operations ---------------------------------------------------------

describe("Inventory operations — quantity (Slice 3)", () => {
  it("receipt: sends the documented body, then shows the refetched stock", async () => {
    let stock: Json[] = [];
    const calls = stubApi(
      baseRoute((request) => {
        if (request.path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (request.path === "/api/v1/inventory/items/i-rope/stock") return { body: page(stock) };
        if (request.path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        if (request.method === "POST" && request.path === "/api/v1/inventory/items/i-rope/receipts") {
          stock = [{ item_id: "i-rope", storage_location_id: "l2", quantity: 25 }];
          return { status: 201, body: movement({ quantity: 25, to_location_id: "l2" }) };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    expect(await screen.findByText("Остатка нет")).toBeInTheDocument();
    const dialog = await openDialog("Принять на склад");
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения"), "Склад клуба › Стеллаж А");
    await userEvent.type(within(dialog).getByLabelText("Количество, м"), "25");
    await userEvent.type(within(dialog).getByLabelText("Стоимость за единицу, ₽"), "100");
    await userEvent.type(within(dialog).getByLabelText("Комментарий"), "Партия 1");
    await submit(dialog, "Принять");

    expect(await screen.findByText("25 м")).toBeInTheDocument();
    expect(await screen.findByText("Поступление принято")).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/items/i-rope/receipts")[0]?.body).toEqual({
      storage_location_id: "l2",
      quantity: 25,
      unit_cost_minor: 10000,
      comment: "Партия 1",
    });
    // The stock and the history are refetched, not patched locally.
    expect(sent(calls, "GET", "/api/v1/inventory/items/i-rope/stock").length).toBeGreaterThanOrEqual(2);
    expect(sent(calls, "GET", "/api/v1/inventory/items/i-rope/movements").length).toBeGreaterThanOrEqual(2);
  });

  it("transfer from a stock row sends from/to/quantity", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.method === "GET" && request.path === "/api/v1/inventory/stock") {
          return { body: page([{ item_id: "i-rope", storage_location_id: "l2", quantity: 7 }]) };
        }
        if (request.method === "POST" && request.path === "/api/v1/inventory/items/i-rope/transfers") {
          return { status: 201, body: movement({ movement_type: "transfer" }) };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory?tab=stock");

    const dialog = await openDialog("Переместить");
    expect(within(dialog).getByLabelText("Откуда")).toHaveValue("l2");
    await userEvent.selectOptions(within(dialog).getByLabelText("Куда"), "Склад клуба › Стеллаж А › Полка 1");
    await userEvent.type(within(dialog).getByLabelText("Количество, м"), "3");
    await submit(dialog, "Переместить");

    await waitFor(() =>
      expect(sent(calls, "POST", "/api/v1/inventory/items/i-rope/transfers")[0]?.body).toEqual({
        from_location_id: "l2",
        to_location_id: "l3",
        quantity: 3,
        comment: null,
      }),
    );
  });

  it("partial write-off requires a reason and shows insufficient stock from the backend", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (request.path === "/api/v1/inventory/items/i-rope/stock") {
          return { body: page([{ item_id: "i-rope", storage_location_id: "l2", quantity: 7 }]) };
        }
        if (request.path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        if (request.method === "POST" && request.path === "/api/v1/inventory/items/i-rope/write-offs") {
          return apiError(409, "insufficient_stock");
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    const stockList = await screen.findByRole("list", { name: "Остатки по местам хранения" });
    await userEvent.click(within(stockList).getByRole("button", { name: "Списать" }));
    const dialog = await screen.findByRole("dialog", { name: "Списать" });
    expect(within(dialog).getByLabelText("Место хранения")).toHaveValue("l2");
    await userEvent.type(within(dialog).getByLabelText("Количество, м"), "9");
    const confirm = within(dialog).getByRole("button", { name: "Списать" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Причина списания"), "Порвана");
    await submit(dialog, "Списать");

    expect(await within(dialog).findByText("Недостаточно остатка для операции.")).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/items/i-rope/write-offs")[0]?.body).toEqual({
      storage_location_id: "l2",
      quantity: 9,
      comment: "Порвана",
    });
  });

  it("reverses a quantity write-off, asking for a location only when the backend requires it", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (request.path === "/api/v1/inventory/items/i-rope/stock") return { body: page([]) };
        if (request.path === "/api/v1/inventory/items/i-rope/movements") {
          return {
            body: page([
              movement({ id: "wo-1", movement_type: "write_off", quantity: 2, from_location_id: "l2", comment: "Брак" }),
              movement({ id: "wo-0", movement_type: "write_off", quantity: 1, from_location_id: "l2" }),
              movement({ id: "rv-0", movement_type: "writeoff_reversal", quantity: 1, reverses_movement_id: "wo-0" }),
            ]),
          };
        }
        if (request.method === "POST" && request.path === "/api/v1/inventory/items/i-rope/write-offs/wo-1/reverse") {
          return request.body?.storage_location_id
            ? { status: 201, body: movement({ movement_type: "writeoff_reversal" }) }
            : apiError(422, "storage_location_required");
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    const history = await screen.findByRole("list", { name: "История движений" });
    expect(within(history).getByText("Списание отменено")).toBeInTheDocument();
    expect(within(history).getAllByRole("button", { name: "Отменить списание" })).toHaveLength(1);
    await userEvent.click(within(history).getByRole("button", { name: "Отменить списание" }));
    const dialog = await screen.findByRole("dialog", { name: "Отменить списание?" });
    await submit(dialog, "Отменить списание");

    expect(
      await within(dialog).findByText("Исходное место хранения в архиве — выберите другое место."),
    ).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения"), "l1");
    await submit(dialog, "Отменить списание");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const reversals = sent(calls, "POST", "/api/v1/inventory/items/i-rope/write-offs/wo-1/reverse");
    expect(reversals.map((call) => call.body)).toEqual([{}, { storage_location_id: "l1" }]);
  });
});

// --- instance operations ---------------------------------------------------------

function instanceRoute(current: () => Json, history: () => Json[] = () => [], extra: Route = () => undefined): Route {
  return baseRoute((request) => {
    const custom = extra(request);
    if (custom) return custom;
    if (request.method === "GET" && request.path === "/api/v1/inventory/instances/inst-1") return { body: current() };
    if (request.method === "GET" && request.path === "/api/v1/inventory/instances/inst-1/movements") {
      return { body: history() };
    }
    return undefined;
  });
}

describe("Inventory operations — instances (Slice 2)", () => {
  it("receives a new instance from an instance-mode item", async () => {
    const calls = stubApi(
      baseRoute((request) => {
        if (request.path === "/api/v1/inventory/items/i-jumar") return { body: JUMAR };
        if (request.method === "POST" && request.path === "/api/v1/inventory/instances") {
          return { status: 201, body: instance({}) };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-jumar");

    const dialog = await openDialog("Принять экземпляр");
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения"), "l2");
    await userEvent.type(within(dialog).getByLabelText("Серийный номер"), "SN-7");
    await submit(dialog, "Принять");

    await waitFor(() =>
      expect(sent(calls, "POST", "/api/v1/inventory/instances")[0]?.body).toEqual({
        item_id: "i-jumar",
        storage_location_id: "l2",
        unit_cost_minor: null,
        manufacturer_barcode: null,
        manufacturer_serial_number: "SN-7",
        description: null,
      }),
    );
  });

  it("transfers, starts and ends repair and writes off through operations; no direct state/location edit", async () => {
    let current = instance({});
    const calls = stubApi(
      instanceRoute(
        () => current,
        () => [],
        (request) => {
          if (request.method !== "POST") return undefined;
          if (request.path.endsWith("/transfer")) current = instance({ storage_location_id: "l3" });
          if (request.path.endsWith("/repair-start")) current = instance({ state: "in_repair", storage_location_id: "l3" });
          if (request.path.endsWith("/repair-end")) current = instance({ state: "available", storage_location_id: "l3" });
          if (request.path.endsWith("/write-off")) current = instance({ state: "written_off", storage_location_id: null });
          return { body: current };
        },
      ),
    );
    await renderAt("/inventory/instances/inst-1");

    const edit = await openDialog("Изменить");
    expect(within(edit).queryByLabelText(/Состояние/)).not.toBeInTheDocument();
    expect(within(edit).queryByLabelText(/Место хранения/)).not.toBeInTheDocument();
    await userEvent.click(within(edit).getByRole("button", { name: "Отмена" }));

    const transfer = await openDialog("Переместить");
    await userEvent.selectOptions(within(transfer).getByLabelText("Куда"), "l3");
    await submit(transfer, "Переместить");
    expect(await screen.findByText("Склад клуба › Стеллаж А › Полка 1")).toBeInTheDocument();

    const start = await openDialog("В ремонт");
    await submit(start, "Начать ремонт");
    expect(await screen.findByRole("button", { name: "Завершить ремонт" })).toBeInTheDocument();

    const end = await openDialog("Завершить ремонт");
    await submit(end, "Завершить ремонт");
    expect(await screen.findByRole("button", { name: "В ремонт" })).toBeInTheDocument();

    const writeOff = await openDialog("Списать");
    await userEvent.type(within(writeOff).getByLabelText("Причина списания"), "Трещина");
    await submit(writeOff, "Списать");
    expect(await screen.findByText(/Экземпляр списан\./)).toBeInTheDocument();

    expect(sent(calls, "POST", "/api/v1/inventory/instances/inst-1/transfer")[0]?.body).toEqual({
      to_location_id: "l3",
      comment: null,
    });
    expect(sent(calls, "POST", "/api/v1/inventory/instances/inst-1/repair-start")).toHaveLength(1);
    expect(sent(calls, "POST", "/api/v1/inventory/instances/inst-1/repair-end")).toHaveLength(1);
    expect(sent(calls, "POST", "/api/v1/inventory/instances/inst-1/write-off")[0]?.body).toEqual({
      comment: "Трещина",
    });
    expect(calls.some((call) => call.method === "PATCH" && call.path.includes("/instances/"))).toBe(false);
  });

  it("reverses the write-off of a written-off instance", async () => {
    let current = instance({ state: "written_off", storage_location_id: null });
    const calls = stubApi(
      instanceRoute(
        () => current,
        () => [
          movement({ id: "rc", item_id: "i-jumar", instance_id: "inst-1", movement_type: "receipt", to_location_id: "l2" }),
          movement({ id: "wo", item_id: "i-jumar", instance_id: "inst-1", movement_type: "write_off", from_location_id: "l2" }),
        ],
        (request) => {
          if (request.method === "POST" && request.path === "/api/v1/inventory/movements/wo/reverse") {
            current = instance({});
            return { body: current };
          }
          return undefined;
        },
      ),
    );
    await renderAt("/inventory/instances/inst-1");

    const dialog = await openDialog("Отменить списание");
    await submit(dialog, "Отменить списание");

    expect(await screen.findByRole("button", { name: "Переместить" })).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/movements/wo/reverse")[0]?.body).toEqual({});
  });

  it("an issued instance offers no direct operations", async () => {
    stubApi(instanceRoute(() => instance({ state: "issued", storage_location_id: null })));
    await renderAt("/inventory/instances/inst-1");

    expect(await screen.findByText(/Экземпляр выдан\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Переместить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Списать" })).not.toBeInTheDocument();
  });
});

// --- issue creation --------------------------------------------------------------

function issueCreateRoute(onCreate: (body: Json) => void): Route {
  return baseRoute((request) => {
    if (request.method === "GET" && request.path === "/api/v1/inventory/instances") {
      return request.search.get("state") === "available"
        ? { body: page([instance({ id: "inst-2", inventory_number: "INV-000002" })]) }
        : { body: page([]) };
    }
    if (request.method === "POST" && request.path === "/api/v1/inventory/issues") {
      onCreate(request.body ?? {});
      return { status: 201, body: issueDetail(issue()) };
    }
    if (request.path === "/api/v1/inventory/issues/iss-1") return { body: issueDetail(issue()) };
    if (request.path === "/api/v1/inventory/issues/iss-1/lines") return { body: page(issue().lines) };
    if (request.path === "/api/v1/inventory/issues/iss-1/movements") return { body: page([]) };
    return undefined;
  });
}

async function chooseItem(dialog: HTMLElement, position: number, name: RegExp) {
  const group = within(dialog).getByRole("group", { name: `Позиция ${position}` });
  await userEvent.selectOptions(
    within(group).getByLabelText("Номенклатура"),
    within(group).getByRole("option", { name }),
  );
  return group;
}

describe("Inventory operations — issue creation (Slice 4)", () => {
  it("issues a quantity item to a Member without an Event and opens the new issue", async () => {
    let body: Json | null = null;
    stubApi(issueCreateRoute((created) => (body = created)));
    await renderAt("/inventory?tab=issues");

    const empty = (await screen.findByText("Выдач пока нет")).parentElement as HTMLElement;
    await userEvent.click(within(empty).getByRole("button", { name: "Выдать" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Участник"), "p-member");
    const group = await chooseItem(dialog, 1, /Верёвка 10 мм/);
    expect(within(group).getByText("Места хранения распределит система.")).toBeInTheDocument();
    expect(within(group).queryByLabelText(/Место хранения/)).not.toBeInTheDocument();
    await userEvent.type(within(group).getByLabelText("Количество, м"), "10");
    await submit(dialog, "Выдать");

    expect(await screen.findByRole("heading", { level: 1, name: /Выдача от/ })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/inventory/issues/iss-1");
    expect(body).toEqual({
      recipient_type: "member",
      recipient_id: "p-member",
      event_id: null,
      planned_return_date: null,
      comment: null,
      lines: [{ item_id: "i-rope", quantity: 10 }],
    });
  });

  it("issues an instance to an Instructor chosen from the instructor directory", async () => {
    let body: Json | null = null;
    const calls = stubApi(issueCreateRoute((created) => (body = created)));
    await renderAt("/inventory?tab=issues");

    const dialog = await openDialog("Выдать");
    await userEvent.selectOptions(within(dialog).getByLabelText("Тип получателя"), "instructor");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Инструктор"), "u-instr");
    const group = await chooseItem(dialog, 1, /Жумар/);
    await userEvent.click(await within(group).findByRole("checkbox", { name: "INV-000002" }));
    await userEvent.type(within(dialog).getByLabelText("Плановая дата возврата"), "2026-10-20");
    await submit(dialog, "Выдать");

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      recipient_type: "instructor",
      recipient_id: "u-instr",
      planned_return_date: "2026-10-20",
      lines: [{ item_id: "i-jumar", instance_ids: ["inst-2"] }],
    });
    expect(sent(calls, "GET", "/api/v1/users").some((call) => call.search.get("role") === "instructor")).toBe(true);
    expect(
      sent(calls, "GET", "/api/v1/inventory/instances").some((call) => call.search.get("state") === "available"),
    ).toBe(true);
  });

  it("issues several lines to a Group with an Event; an item is not offered twice", async () => {
    let body: Json | null = null;
    stubApi(issueCreateRoute((created) => (body = created)));
    await renderAt("/inventory?tab=issues");

    const dialog = await openDialog("Выдать");
    await userEvent.selectOptions(within(dialog).getByLabelText("Тип получателя"), "group");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Группа"), "g1");
    await userEvent.selectOptions(await within(dialog).findByLabelText("Мероприятие"), "e1");
    await userEvent.type(within(dialog).getByLabelText("Комментарий"), "Сборы");
    const first = await chooseItem(dialog, 1, /Верёвка 10 мм/);
    await userEvent.type(within(first).getByLabelText("Количество, м"), "4");
    await userEvent.click(within(dialog).getByRole("button", { name: "Добавить позицию" }));
    const second = within(dialog).getByRole("group", { name: "Позиция 2" });
    expect(within(second).queryByRole("option", { name: /Верёвка 10 мм/ })).not.toBeInTheDocument();
    await chooseItem(dialog, 2, /Жумар/);
    await userEvent.click(await within(second).findByRole("checkbox", { name: "INV-000002" }));
    await submit(dialog, "Выдать");

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({
      recipient_type: "group",
      recipient_id: "g1",
      event_id: "e1",
      planned_return_date: null,
      comment: "Сборы",
      lines: [
        { item_id: "i-rope", quantity: 4 },
        { item_id: "i-jumar", instance_ids: ["inst-2"] },
      ],
    });
  });

  it("lists issues with recipient, type, Event, dates and the backend's outstanding flag", async () => {
    const calls = stubApi(
      baseRoute((request) =>
        request.method === "GET" && request.path === "/api/v1/inventory/issues"
          ? {
              body: page([
                issueDetail(issue({ event_id: "e1" })),
                issueDetail(issue({ id: "iss-2", recipient_type: "group", recipient_id: "g1", status: "cancelled", has_outstanding: false, comment: null })),
              ]),
            }
          : undefined,
      ),
    );
    await renderAt("/inventory?tab=issues");

    const list = await screen.findByRole("list", { name: "Выдачи" });
    expect(await within(list).findByRole("link", { name: "Сидоров Пётр" })).toHaveAttribute("href", "/inventory/issues/iss-1");
    expect(await within(list).findByRole("link", { name: "Отряд «Север»" })).toBeInTheDocument();
    expect(await within(list).findByText(/Поход на Эльбрус/)).toBeInTheDocument();
    expect(within(list).getByText("Есть невозвращённое")).toBeInTheDocument();
    expect(within(list).getByText("Отменена")).toBeInTheDocument();
    expect(within(list).getAllByText("10 октября 2026 г.", { exact: false })).toHaveLength(2);

    await userEvent.selectOptions(screen.getByLabelText("Тип получателя"), "group");
    await waitFor(() =>
      expect(
        sent(calls, "GET", "/api/v1/inventory/issues").some((call) => call.search.get("recipient_type") === "group"),
      ).toBe(true),
    );
  });
});

// --- issue detail: return, cancel, edit, remove line, lost ---------------------------

/** A fake issue document whose balances change only when the fake
 * backend applies an operation — the UI must show what it refetches. */
function issueServer(initial: IssueState, handle: (request: Request, state: IssueState) => Reply | undefined = () => undefined) {
  let state = initial;
  const route = baseRoute((request) => {
    if (request.method === "GET" && request.path === "/api/v1/inventory/issues/iss-1") return { body: issueDetail(state) };
    if (request.method === "GET" && request.path === "/api/v1/inventory/issues/iss-1/lines") {
      const status = request.search.get("status") ?? "active";
      const lines = status === "active" ? state.lines : status === "removed" ? state.removed : [...state.lines, ...state.removed];
      return { body: page(lines) };
    }
    if (request.method === "GET" && request.path === "/api/v1/inventory/issues/iss-1/movements") return { body: page([]) };
    if (request.method === "GET" && request.path.startsWith("/api/v1/inventory/instances/")) {
      const id = request.path.split("/").pop() ?? "";
      return { body: instance({ id, inventory_number: id === "inst-2" ? "INV-000002" : "INV-000003", state: "issued" }) };
    }
    const reply = handle(request, state);
    if (reply && typeof reply === "object" && "body" in reply && reply.body && (reply.status ?? 200) < 300) {
      state = reply.body as IssueState;
      return { status: reply.status, body: issueDetail(state) };
    }
    return reply;
  });
  return { route, current: () => state };
}

function applyQuantityReturn(state: IssueState, lineId: string, quantity: number): IssueState {
  const lines = state.lines.map((entry) =>
    entry.id === lineId
      ? {
          ...entry,
          returned_quantity: (entry.returned_quantity as number) + quantity,
          outstanding_quantity: (entry.outstanding_quantity as number) - quantity,
        }
      : entry,
  );
  return { ...state, lines, has_outstanding: lines.some((entry) => (entry.outstanding_quantity as number) > 0) };
}

describe("Inventory operations — return (Slice 4)", () => {
  it("partial, repeated partial and full return of a quantity line into a chosen location", async () => {
    const server = issueServer(issue(), (request, state) => {
      if (request.method !== "POST" || request.path !== "/api/v1/inventory/issues/iss-1/returns") return undefined;
      const [entry] = request.body?.quantities as Array<{ line_id: string; quantity: number }>;
      return { body: applyQuantityReturn(state, entry.line_id, entry.quantity) };
    });
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const lines = await screen.findByRole("list", { name: "Строки выдачи" });
    expect(within(lines).getByText("Числится выданным").nextElementSibling).toHaveTextContent("10 м");

    for (const [amount, location, left] of [
      ["6", "l2", "4 м"],
      ["3", "l3", "1 м"],
      ["1", "l1", "0 м"],
    ] as const) {
      await userEvent.click(within(lines).getByRole("button", { name: "Вернуть" }));
      const dialog = await screen.findByRole("dialog", { name: "Вернуть" });
      await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения для возврата"), location);
      await userEvent.type(within(dialog).getByLabelText("Верёвка 10 мм, м"), amount);
      await submit(dialog, "Вернуть");
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      const outstanding = within(lines).getByText("Числится выданным").nextElementSibling;
      await waitFor(() => expect(outstanding).toHaveTextContent(left));
    }

    // Fully returned: the backend reports nothing outstanding; the issue is read-only.
    expect(await screen.findByText("Всё имущество возвращено — выдача больше не изменяется.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуть всё" })).not.toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/issues/iss-1/returns").map((call) => call.body)).toEqual([
      { storage_location_id: "l2", quantities: [{ line_id: "ln-rope", quantity: 6 }], instance_ids: [], comment: null },
      { storage_location_id: "l3", quantities: [{ line_id: "ln-rope", quantity: 3 }], instance_ids: [], comment: null },
      { storage_location_id: "l1", quantities: [{ line_id: "ln-rope", quantity: 1 }], instance_ids: [], comment: null },
    ]);
  });

  it("returns a specific instance and shows a backend over-return refusal", async () => {
    let refuse = true;
    const instanceLine = line({
      id: "ln-jumar",
      item_id: "i-jumar",
      accounting_mode: "instance",
      issued_quantity: 2,
      outstanding_quantity: 2,
      outstanding_instance_ids: ["inst-2", "inst-3"],
    });
    const server = issueServer(issue({ lines: [instanceLine] }), (request, state) => {
      if (request.method !== "POST" || !request.path.endsWith("/returns")) return undefined;
      if (refuse) return apiError(409, "return_exceeds_outstanding");
      return { body: { ...state, lines: [{ ...instanceLine, outstanding_quantity: 1, returned_quantity: 1, outstanding_instance_ids: ["inst-3"] }] } };
    });
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Вернуть");
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения для возврата"), "l2");
    await userEvent.click(await within(dialog).findByRole("checkbox", { name: "INV-000002" }));
    await submit(dialog, "Вернуть");
    expect(await within(dialog).findByText("Нельзя вернуть больше, чем числится выданным.")).toBeInTheDocument();

    refuse = false;
    await submit(dialog, "Вернуть");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const issued = await screen.findByRole("list", { name: "Выданные экземпляры" });
    await waitFor(() => expect(within(issued).queryByRole("link", { name: "INV-000002" })).not.toBeInTheDocument());
    expect(sent(calls, "POST", "/api/v1/inventory/issues/iss-1/returns")[1]?.body).toEqual({
      storage_location_id: "l2",
      quantities: [],
      instance_ids: ["inst-2"],
      comment: null,
    });
  });

  it("«Вернуть всё» sends one return request with everything the backend reports outstanding", async () => {
    const server = issueServer(
      issue({
        lines: [
          line({ returned_quantity: 6, outstanding_quantity: 4 }),
          line({ id: "ln-jumar", item_id: "i-jumar", accounting_mode: "instance", issued_quantity: 1, outstanding_quantity: 1, outstanding_instance_ids: ["inst-2"] }),
        ],
      }),
      (request, state) =>
        request.method === "POST" && request.path.endsWith("/returns")
          ? { body: { ...state, has_outstanding: false, lines: state.lines.map((entry) => ({ ...entry, outstanding_quantity: 0, outstanding_instance_ids: [] })) } }
          : undefined,
    );
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Вернуть всё");
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения для возврата"), "l1");
    await submit(dialog, "Вернуть всё");

    expect(await screen.findByText("Всё имущество возвращено — выдача больше не изменяется.")).toBeInTheDocument();
    const returns = sent(calls, "POST", "/api/v1/inventory/issues/iss-1/returns");
    expect(returns).toHaveLength(1);
    expect(returns[0]?.body).toEqual({
      storage_location_id: "l1",
      quantities: [{ line_id: "ln-rope", quantity: 4 }],
      instance_ids: ["inst-2"],
      comment: null,
    });
  });
});

describe("Inventory operations — issue lifecycle (Slice 4)", () => {
  it("cancels an issue into a chosen location; the backend returns the property", async () => {
    const server = issueServer(issue(), (request, state) =>
      request.method === "POST" && request.path === "/api/v1/inventory/issues/iss-1/cancel"
        ? { body: { ...state, status: "cancelled", has_outstanding: false, cancelled_at: "2026-10-02T10:00:00Z" } }
        : undefined,
    );
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Отменить выдачу");
    expect(within(dialog).getByRole("button", { name: "Отменить выдачу" })).toBeDisabled();
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения для возврата"), "l2");
    await submit(dialog, "Отменить выдачу");

    expect(await screen.findByText("Выдача отменена и больше не изменяется.")).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/issues/iss-1/cancel")[0]?.body).toEqual({ storage_location_id: "l2" });
    expect(calls.some((call) => call.path.endsWith("/returns"))).toBe(false);
  });

  it("edits the header with only the changed fields and adds a line", async () => {
    const server = issueServer(issue(), (request, state) => {
      if (request.method === "PATCH" && request.path === "/api/v1/inventory/issues/iss-1") {
        return { body: { ...state, ...request.body } };
      }
      if (request.method === "POST" && request.path === "/api/v1/inventory/issues/iss-1/lines") {
        return { status: 201, body: state };
      }
      return undefined;
    });
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const edit = await openDialog("Изменить");
    const comment = within(edit).getByLabelText("Комментарий");
    expect(comment).toHaveValue("На сборы");
    await userEvent.clear(comment);
    await userEvent.type(comment, "На сборы и поход");
    await submit(edit, "Сохранить");
    await waitFor(() =>
      expect(sent(calls, "PATCH", "/api/v1/inventory/issues/iss-1")[0]?.body).toEqual({ comment: "На сборы и поход" }),
    );
    expect(await screen.findByText("На сборы и поход")).toBeInTheDocument();

    const add = await openDialog("Добавить позиции");
    const group = await chooseItem(add, 1, /Верёвка 10 мм/);
    await userEvent.type(within(group).getByLabelText("Количество, м"), "2");
    await submit(add, "Выдать");
    await waitFor(() =>
      expect(sent(calls, "POST", "/api/v1/inventory/issues/iss-1/lines")[0]?.body).toEqual({
        lines: [{ item_id: "i-rope", quantity: 2 }],
      }),
    );
  });

  it("offers «Удалить строку» only on a line with nothing outstanding and shows removed lines as history", async () => {
    const returned = line({ id: "ln-done", item_id: "i-jumar", accounting_mode: "instance", issued_quantity: 1, returned_quantity: 1, outstanding_quantity: 0 });
    const server = issueServer(issue({ lines: [line({}), returned] }), (request, state) => {
      if (request.method === "DELETE" && request.path === "/api/v1/inventory/issues/iss-1/lines/ln-done") {
        const removedLine = { ...returned, removed_at: "2026-10-02T11:00:00Z", removed_by: "u1" };
        return { body: { ...state, lines: [state.lines[0]], removed: [removedLine] } };
      }
      return undefined;
    });
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const lines = await screen.findByRole("list", { name: "Строки выдачи" });
    const [outstandingCard, doneCard] = within(lines).getAllByRole("listitem");
    expect(within(outstandingCard).queryByRole("button", { name: "Удалить строку" })).not.toBeInTheDocument();
    await userEvent.click(within(doneCard).getByRole("button", { name: "Удалить строку" }));
    const dialog = await screen.findByRole("dialog", { name: "Удалить строку?" });
    await submit(dialog, "Удалить строку");

    await waitFor(() => expect(within(lines).getAllByRole("listitem")).toHaveLength(1));
    expect(sent(calls, "DELETE", "/api/v1/inventory/issues/iss-1/lines/ln-done")).toHaveLength(1);

    await userEvent.selectOptions(screen.getByLabelText("Строки"), "removed");
    const removedList = await screen.findByRole("list", { name: "Строки выдачи" });
    expect(await within(removedList).findByText(/^Удалена /)).toBeInTheDocument();
    expect(within(removedList).queryByRole("button")).not.toBeInTheDocument();
    expect(
      sent(calls, "GET", "/api/v1/inventory/issues/iss-1/lines").some((call) => call.search.get("status") === "removed"),
    ).toBe(true);
  });

  it("shows the backend refusal to remove a line", async () => {
    const returned = line({ id: "ln-done", outstanding_quantity: 0, returned_quantity: 10 });
    const server = issueServer(issue({ lines: [line({ id: "ln-other", item_id: "i-jumar", accounting_mode: "instance", outstanding_instance_ids: ["inst-2"], outstanding_quantity: 1 }), returned] }), (request) =>
      request.method === "DELETE" ? apiError(409, "issue_fully_returned") : undefined,
    );
    stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Удалить строку");
    await submit(dialog, "Удалить строку");
    expect(await within(dialog).findByText("Выдача полностью возвращена и больше не изменяется.")).toBeInTheDocument();
  });

  it("reports a lost instance with a mandatory reason and a location; no client-side state", async () => {
    const instanceLine = line({ id: "ln-jumar", item_id: "i-jumar", accounting_mode: "instance", issued_quantity: 1, outstanding_quantity: 1, outstanding_instance_ids: ["inst-2"] });
    const server = issueServer(issue({ lines: [instanceLine] }), (request, state) =>
      request.method === "POST" && request.path === "/api/v1/inventory/issues/iss-1/lost"
        ? { body: { ...state, has_outstanding: false, lines: [{ ...instanceLine, outstanding_quantity: 0, returned_quantity: 1, outstanding_instance_ids: [] }] } }
        : undefined,
    );
    const calls = stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Утерян");
    const confirm = within(dialog).getByRole("button", { name: "Оформить утерю" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения"), "l2");
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Причина утери"), "Упал в трещину");
    await userEvent.type(within(dialog).getByLabelText("Комментарий"), "Со слов группы");
    await submit(dialog, "Оформить утерю");

    expect(await screen.findByText("Утеря оформлена")).toBeInTheDocument();
    expect(sent(calls, "POST", "/api/v1/inventory/issues/iss-1/lost")[0]?.body).toEqual({
      instance_id: "inst-2",
      storage_location_id: "l2",
      reason: "Упал в трещину",
      comment: "Со слов группы",
    });
    expect(calls.some((call) => call.path.includes("/write-off"))).toBe(false);
  });
});

// --- errors ----------------------------------------------------------------------

describe("Inventory operations — errors", () => {
  it.each([
    [{ status: 403, body: { error: { code: "forbidden", message: "Forbidden" } } }, "Операция доступна только администратору."],
    [apiError(404, "not_found"), "Запись не найдена — возможно, её уже изменили. Обновите страницу."],
    [apiError(409, "issue_cancelled"), "Выдача отменена и больше не изменяется."],
    [
      { status: 422, body: { error: { code: "validation_error", message: "Request validation failed", details: { fields: [] } } } },
      "Проверьте введённые данные.",
    ],
    [apiError(422, "invalid_recipient"), "Получатель не подходит для выдачи."],
    [apiError(500, "internal_error"), "Сервер не смог выполнить операцию. Попробуйте ещё раз."],
    ["network-error" as const, "Нет связи с сервером. Проверьте подключение и попробуйте ещё раз."],
  ])("maps a failed cancel (%#) to a clear message and keeps the dialog open", async (reply, message) => {
    const server = issueServer(issue(), (request) =>
      request.method === "POST" && request.path.endsWith("/cancel") ? reply : undefined,
    );
    stubApi(server.route);
    await renderAt("/inventory/issues/iss-1");

    const dialog = await openDialog("Отменить выдачу");
    await userEvent.selectOptions(within(dialog).getByLabelText("Место хранения для возврата"), "l2");
    await submit(dialog, "Отменить выдачу");

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it.each([["instructor"], ["member"]])(
    "%s gets the 403 state on an issue page and sends no inventory request",
    async (role) => {
      const calls = stubApi(baseRoute(() => undefined), {
        ...ME,
        role_assignments: [{ role_code: role, club_id: "club-1", scope_type: "all" }],
      });
      await renderAt("/inventory/issues/iss-1");

      expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
      expect(calls.some((call) => call.path.startsWith("/api/v1/inventory"))).toBe(false);
    },
  );

  it("a missing issue shows «не найдено»", async () => {
    stubApi(baseRoute((request) => (request.path === "/api/v1/inventory/issues/iss-404" ? apiError(404, "not_found") : undefined)));
    await renderAt("/inventory/issues/iss-404");

    expect(await screen.findByText("Запись не найдена.", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
