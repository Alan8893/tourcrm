import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Inventory UI («Склад», Issue #230) rendered through the real App —
 * AppShell, the Administrator guard and the real API client — with only
 * `fetch` stubbed. */

type Reply = { status?: number; body?: unknown } | "pending";
type Router = (path: string, search: URLSearchParams) => Reply | undefined;

function page<T>(items: T[], pageNumber = 1, pages = items.length ? 1 : 0) {
  return { items, pagination: { page: pageNumber, page_size: 20, total: items.length, pages } };
}

function me(roleCodes: string[]) {
  return {
    user: {
      id: "u1",
      login_identifier: "admin@example.com",
      status: "active",
      email_verified_at: null,
      person: {
        id: "p1",
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

const CATEGORIES = [{ id: "c1", name: "Страховочное снаряжение", status: "active" }];
const UNITS = [
  { id: "un1", name: "шт", is_system: true, status: "active" },
  { id: "un2", name: "м", is_system: true, status: "active" },
];
const LOCATIONS = [
  { id: "l1", parent_id: null, name: "Склад клуба", status: "active" },
  { id: "l2", parent_id: "l1", name: "Стеллаж А", status: "active" },
  { id: "l3", parent_id: null, name: "Старый подвал", status: "archived" },
  { id: "l4", parent_id: "l1", name: "Полка Б", status: "archived" },
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
const INSTANCE = {
  id: "inst-1",
  item_id: "i-jumar",
  inventory_number: "INV-000001",
  manufacturer_barcode: "4600000000017",
  manufacturer_serial_number: "SN-42",
  description: "Левый",
  state: "in_repair",
  storage_location_id: "l2",
};

function movement(overrides: Record<string, unknown>) {
  return {
    id: `m-${Math.random()}`,
    item_id: "i-rope",
    instance_id: null,
    movement_type: "receipt",
    from_location_id: null,
    to_location_id: null,
    quantity: null,
    unit_cost_minor: null,
    comment: null,
    reverses_movement_id: null,
    created_by: "u1",
    created_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

/** Reference data every inventory page loads; `overrides` wins. */
function inventoryApi(overrides: Router = () => undefined): Router {
  return (path, search) => {
    const custom = overrides(path, search);
    if (custom) return custom;
    switch (path) {
      case "/api/v1/inventory/categories":
        return { body: page(CATEGORIES) };
      case "/api/v1/inventory/units":
        return { body: page(UNITS) };
      case "/api/v1/inventory/storage-locations":
        return { body: page(LOCATIONS) };
      case "/api/v1/inventory/items":
        return { body: page([ROPE, JUMAR]) };
      default:
        return undefined;
    }
  };
}

function stubApi(roleCodes: string[] | null, router: Router) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    const reply: Reply | undefined =
      url.pathname === "/api/v1/auth/me"
        ? roleCodes
          ? { body: me(roleCodes) }
          : { status: 401, body: { error: { code: "unauthenticated", message: "Нужен вход" } } }
        : router(url.pathname, url.searchParams);
    if (reply === "pending") return new Promise<Response>(() => {});
    if (!reply) throw new Error(`No stub for ${url.pathname}${url.search}`);
    return new Response(JSON.stringify(reply.body ?? {}), {
      status: reply.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  // App owns a module-level QueryClient; a fresh module per test keeps
  // cached responses of another test out.
  vi.resetModules();
  const { App } = await import("../App");
  render(<App />);
}

function requestedInventory(fetchMock: ReturnType<typeof stubApi>) {
  return fetchMock.mock.calls.some(([input]) => String(input).includes("/inventory"));
}

/** Query parameters of every request made to `path`, in order. */
function requestParams(fetchMock: ReturnType<typeof stubApi>, path: string): URLSearchParams[] {
  return fetchMock.mock.calls
    .map(([input]) => new URL(String(input), "http://localhost"))
    .filter((url) => url.pathname === path)
    .map((url) => url.searchParams);
}

/** Russian digit grouping uses a (narrow) no-break space. */
const GROUPED_1234567 = /^1[\s\u00a0\u202f]234[\s\u00a0\u202f]567 м$/;

/** The App's QueryClient retries a failed query once (after ~1 s) before
 * reporting the error. */
const RETRIED = { timeout: 5000 };

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("Inventory — access", () => {
  it("Administrator opens «Склад»", async () => {
    stubApi(["admin"], inventoryApi());
    await renderAt("/inventory");

    expect(await screen.findByRole("heading", { level: 1, name: "Склад" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Верёвка 10 мм" })).toBeInTheDocument();
  });

  it.each([["instructor"], ["member"], ["guardian"]])(
    "%s gets the 403 state, no «Склад» navigation item and no inventory request",
    async (role) => {
      const fetchMock = stubApi([role], inventoryApi());
      await renderAt("/inventory");

      expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Склад" })).not.toBeInTheDocument();
      const nav = screen.getByRole("navigation", { name: "Основная навигация" });
      expect(within(nav).queryByRole("link", { name: "Склад" })).not.toBeInTheDocument();
      expect(requestedInventory(fetchMock)).toBe(false);
    },
  );

  it.each([
    ["instructor", "/inventory/items/i-rope"],
    ["member", "/inventory/items/i-rope"],
    ["guardian", "/inventory/items/i-rope"],
    ["instructor", "/inventory/instances/inst-1"],
    ["member", "/inventory/instances/inst-1"],
    ["guardian", "/inventory/instances/inst-1"],
  ])("%s gets the 403 state on %s and no inventory request", async (role, path) => {
    const fetchMock = stubApi([role], inventoryApi());
    await renderAt(path);

    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
    expect(requestedInventory(fetchMock)).toBe(false);
  });

  it("Administrator sees «Склад» in the navigation between «Отчёты» and «Настройки»", async () => {
    stubApi(["admin"], inventoryApi());
    await renderAt("/inventory");

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    const labels = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(labels.slice(labels.indexOf("Отчёты"), labels.indexOf("Отчёты") + 3)).toEqual([
      "Отчёты",
      "Склад",
      "Настройки",
    ]);
    const inventoryLink = within(nav).getByRole("link", { name: "Склад" });
    expect(inventoryLink).toHaveAttribute("href", "/inventory");
    expect(inventoryLink).toHaveAttribute("aria-current", "page");
    expect(inventoryLink.querySelector("img")?.getAttribute("src")).toBe(
      "/assets/ui/icons/navigation/inventory/inventory_24px.webp",
    );
  });

  it("multi-role Guardian + Administrator opens «Склад»", async () => {
    stubApi(["guardian", "admin"], inventoryApi());
    await renderAt("/inventory");

    expect(await screen.findByRole("heading", { level: 1, name: "Склад" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getByRole("link", { name: "Склад" })).toBeInTheDocument();
    expect(screen.queryByText("Раздел недоступен")).not.toBeInTheDocument();
  });

  it("sends an unauthenticated visitor to /login", async () => {
    const fetchMock = stubApi(null, inventoryApi());
    await renderAt("/inventory/items/i-rope");

    expect(await screen.findByRole("button", { name: "Войти" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(new URLSearchParams(window.location.search).get("next")).toBe("/inventory/items/i-rope");
    expect(requestedInventory(fetchMock)).toBe(false);
  });

  it("a 403 from the inventory API shows the forbidden state", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) =>
        path === "/api/v1/inventory/categories"
          ? { status: 403, body: { error: { code: "forbidden", message: "Нет доступа" } } }
          : undefined,
      ),
    );
    await renderAt("/inventory");

    expect(
      await screen.findByText("Склад доступен только администратору.", {}, RETRIED),
    ).toBeInTheDocument();
  });

  it("a 401 from the inventory API re-checks the session and leads to /login", async () => {
    let signedIn = true;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/auth/me") {
        return new Response(JSON.stringify(signedIn ? me(["admin"]) : {}), {
          status: signedIn ? 200 : 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      signedIn = false;
      return new Response(JSON.stringify({ error: { code: "unauthenticated", message: "Нужен вход" } }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    await renderAt("/inventory");

    expect(await screen.findByRole("button", { name: "Войти" }, RETRIED)).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });
});

describe("Inventory — states", () => {
  it("shows a loading state while the inventory loads", async () => {
    stubApi(["admin"], inventoryApi((path) => (path === "/api/v1/inventory/items" ? "pending" : undefined)));
    await renderAt("/inventory");

    expect(await screen.findByText("Загружаем склад…")).toBeInTheDocument();
  });

  it("shows an empty state when there are no items", async () => {
    stubApi(["admin"], inventoryApi((path) => (path === "/api/v1/inventory/items" ? { body: page([]) } : undefined)));
    await renderAt("/inventory");

    const title = await screen.findByText("Номенклатуры пока нет");
    expect(title.parentElement?.querySelector("img")?.getAttribute("src")).toBe(
      "/assets/ui/illustrations/empty-inventory_128px.webp",
    );
  });

  it("shows an error state and retries", async () => {
    let failing = true;
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path) =>
        path === "/api/v1/inventory/units" && failing
          ? { status: 500, body: { error: { code: "internal_error", message: "Сервер недоступен" } } }
          : undefined,
      ),
    );
    await renderAt("/inventory");

    expect(await screen.findByText("Не удалось загрузить склад", {}, RETRIED)).toBeInTheDocument();
    expect(screen.getByText("Сервер недоступен")).toBeInTheDocument();

    const unitRequests = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).includes("/inventory/units")).length;
    const failedAttempts = unitRequests();
    failing = false;
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("link", { name: "Жумар" })).toBeInTheDocument();
    expect(unitRequests()).toBe(failedAttempts + 1);
  });
});

describe("Inventory — overview", () => {
  it("lists items with accounting mode, category, unit, cost and status", async () => {
    stubApi(["admin"], inventoryApi());
    await renderAt("/inventory");

    const list = await screen.findByRole("list", { name: "Номенклатура" });
    const [rope, jumar] = within(list).getAllByRole("listitem");
    expect(within(rope).getByText("Количественный учёт")).toBeInTheDocument();
    expect(within(rope).getByText("Страховочное снаряжение")).toBeInTheDocument();
    expect(within(rope).getByText("м")).toBeInTheDocument();
    expect(within(rope).getByText(/120,50\s₽/)).toBeInTheDocument();
    expect(within(rope).getByText("Активна")).toBeInTheDocument();
    expect(within(jumar).getByText("Поэкземплярный учёт")).toBeInTheDocument();
    expect(within(jumar).getByText("не указана")).toBeInTheDocument();
  });

  it("requests items by the chosen status", async () => {
    const fetchMock = stubApi(["admin"], inventoryApi());
    await renderAt("/inventory");

    await userEvent.selectOptions(await screen.findByLabelText("Статус"), "archived");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => /\/inventory\/items\?status=archived&page=1/.test(String(input))),
      ).toBe(true),
    );
  });

  it("shows storage locations as a tree", async () => {
    stubApi(["admin"], inventoryApi());
    await renderAt("/inventory?tab=locations");

    const tree = await screen.findByRole("list", { name: "Места хранения" });
    const root = within(tree).getByText("Склад клуба").closest("li");
    expect(root).not.toBeNull();
    expect(within(root as HTMLElement).getByText("Стеллаж А")).toBeInTheDocument();
    expect(within(tree).queryByText("Старый подвал")).not.toBeInTheDocument();
  });

  it("shows quantity stock by location", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) =>
        path === "/api/v1/inventory/stock"
          ? { body: page([{ item_id: "i-rope", storage_location_id: "l2", quantity: 150 }]) }
          : undefined,
      ),
    );
    await renderAt("/inventory?tab=stock");

    const list = await screen.findByRole("list", { name: "Остатки" });
    expect(within(list).getByRole("link", { name: "Верёвка 10 мм" })).toBeInTheDocument();
    expect(within(list).getByText("150 м")).toBeInTheDocument();
    expect(within(list).getByText("Склад клуба › Стеллаж А")).toBeInTheDocument();
  });

  it("shows instances with inventory number, barcode, serial number, state, location and description", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => (path === "/api/v1/inventory/instances" ? { body: page([INSTANCE]) } : undefined)),
    );
    await renderAt("/inventory?tab=instances");

    const list = await screen.findByRole("list", { name: "Экземпляры" });
    expect(within(list).getByRole("link", { name: "INV-000001" })).toBeInTheDocument();
    expect(within(list).getByText("4600000000017")).toBeInTheDocument();
    expect(within(list).getByText("SN-42")).toBeInTheDocument();
    expect(within(list).getByText("В ремонте")).toBeInTheDocument();
    expect(within(list).getByText("Склад клуба › Стеллаж А")).toBeInTheDocument();
    expect(within(list).getByText("Левый")).toBeInTheDocument();
  });

  it("renders lists as list markup, without a <table> element", async () => {
    stubApi(["admin"], inventoryApi());
    await renderAt("/inventory");

    await screen.findByRole("list", { name: "Номенклатура" });
    expect(document.querySelector("table")).toBeNull();
  });
});

describe("Inventory — item pages and movement history", () => {
  it("a quantity item shows its stock and movement history", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        switch (path) {
          case "/api/v1/inventory/items/i-rope":
            return { body: ROPE };
          case "/api/v1/inventory/items/i-rope/stock":
            return { body: page([{ item_id: "i-rope", storage_location_id: "l1", quantity: 40 }]) };
          case "/api/v1/inventory/items/i-rope/movements":
            return {
              body: page([
                movement({ movement_type: "receipt", to_location_id: "l1", quantity: 50, unit_cost_minor: 11000 }),
                movement({
                  movement_type: "write_off",
                  from_location_id: "l1",
                  quantity: 10,
                  comment: "Перетёрлась",
                  created_at: "2026-09-02T10:00:00Z",
                }),
              ]),
            };
          default:
            return undefined;
        }
      }),
    );
    await renderAt("/inventory/items/i-rope");

    expect(await screen.findByRole("heading", { level: 1, name: "Верёвка 10 мм" })).toBeInTheDocument();
    const stock = await screen.findByRole("list", { name: "Остатки по местам хранения" });
    expect(within(stock).getByText("40 м")).toBeInTheDocument();

    const history = await screen.findByRole("list", { name: "История движений" });
    const [receipt, writeOff] = within(history).getAllByRole("listitem");
    expect(within(receipt).getByRole("heading", { name: "Приём" })).toBeInTheDocument();
    expect(within(receipt).getByText("50 м")).toBeInTheDocument();
    expect(within(receipt).getByText(/110,00\s₽/)).toBeInTheDocument();
    expect(within(writeOff).getByRole("heading", { name: "Списание" })).toBeInTheDocument();
    expect(within(writeOff).getByText("Перетёрлась")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Экземпляры" })).not.toBeInTheDocument();
  });

  it("an instance item lists its instances and requests no quantity endpoints", async () => {
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path, search) => {
        if (path === "/api/v1/inventory/items/i-jumar") return { body: JUMAR };
        if (path === "/api/v1/inventory/instances" && search.get("item_id") === "i-jumar") {
          return { body: page([INSTANCE]) };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-jumar");

    expect(await screen.findByRole("heading", { level: 1, name: "Жумар" })).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: "Экземпляры" });
    expect(within(list).getByRole("link", { name: "INV-000001" })).toBeInTheDocument();
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("/items/i-jumar/stock"))).toBe(false);
    expect(urls.some((url) => url.includes("/items/i-jumar/movements"))).toBe(false);
  });

  it("an instance page shows the instance and its movement history", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        if (path === "/api/v1/inventory/instances/inst-1") return { body: INSTANCE };
        if (path === "/api/v1/inventory/instances/inst-1/movements") {
          return {
            body: [
              movement({ item_id: "i-jumar", instance_id: "inst-1", movement_type: "receipt", to_location_id: "l1" }),
              movement({
                item_id: "i-jumar",
                instance_id: "inst-1",
                movement_type: "transfer",
                from_location_id: "l1",
                to_location_id: "l2",
              }),
              movement({ item_id: "i-jumar", instance_id: "inst-1", movement_type: "repair_start" }),
            ],
          };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/instances/inst-1");

    expect(await screen.findByRole("heading", { level: 1, name: "INV-000001" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Жумар" })).toHaveAttribute("href", "/inventory/items/i-jumar");
    const history = await screen.findByRole("list", { name: "История движений" });
    expect(within(history).getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Приём",
      "Перемещение",
      "Начало ремонта",
    ]);
    expect(within(history).getByText("Склад клуба › Стеллаж А")).toBeInTheDocument();
  });

  it("an item without movements shows an empty history", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        if (path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (path === "/api/v1/inventory/items/i-rope/stock") return { body: page([]) };
        if (path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    expect(await screen.findByText("Движений пока нет")).toBeInTheDocument();
    expect(screen.getByText("Остатка нет")).toBeInTheDocument();
  });
});

describe("Inventory — filters, pagination and review fixes", () => {
  it("requests the next items page through Pagination", async () => {
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path, search) => {
        if (path !== "/api/v1/inventory/items" || search.get("page_size") === "200") return undefined;
        const pageNumber = Number(search.get("page"));
        return { body: page([pageNumber === 1 ? ROPE : JUMAR], pageNumber, 2) };
      }),
    );
    await renderAt("/inventory");

    await screen.findByRole("link", { name: "Верёвка 10 мм" });
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(await screen.findByRole("link", { name: "Жумар" })).toBeInTheDocument();
    const last = requestParams(fetchMock, "/api/v1/inventory/items").at(-1);
    expect(last?.get("page")).toBe("2");
    expect(last?.get("page_size")).toBe("20");
    expect(last?.get("status")).toBe("active");
  });

  it("sends the stock filters item_id and storage_location_id", async () => {
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path) => (path === "/api/v1/inventory/stock" ? { body: page([]) } : undefined)),
    );
    await renderAt("/inventory?tab=stock");

    await userEvent.selectOptions(await screen.findByLabelText("Номенклатура"), "i-rope");
    await userEvent.selectOptions(screen.getByLabelText("Место хранения"), "l2");
    await waitFor(() => {
      const last = requestParams(fetchMock, "/api/v1/inventory/stock").at(-1);
      expect(last?.get("item_id")).toBe("i-rope");
      expect(last?.get("storage_location_id")).toBe("l2");
      expect(last?.get("page")).toBe("1");
    });
  });

  it("sends the instance filters item_id, state=written_off and storage_location_id", async () => {
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path) => (path === "/api/v1/inventory/instances" ? { body: page([]) } : undefined)),
    );
    await renderAt("/inventory?tab=instances");

    await userEvent.selectOptions(await screen.findByLabelText("Номенклатура"), "i-jumar");
    await userEvent.selectOptions(screen.getByLabelText("Состояние"), "written_off");
    await userEvent.selectOptions(screen.getByLabelText("Место хранения"), "l2");
    await waitFor(() => {
      const last = requestParams(fetchMock, "/api/v1/inventory/instances").at(-1);
      expect(last?.get("item_id")).toBe("i-jumar");
      expect(last?.get("state")).toBe("written_off");
      expect(last?.get("storage_location_id")).toBe("l2");
    });
  });

  it("filters storage locations without a second request and keeps an archived node's place", async () => {
    const fetchMock = stubApi(["admin"], inventoryApi());
    await renderAt("/inventory?tab=locations");

    await screen.findByRole("list", { name: "Места хранения" });
    await userEvent.selectOptions(screen.getByLabelText("Статус"), "archived");
    const tree = screen.getByRole("list", { name: "Места хранения" });
    expect(within(tree).getByText("Полка Б")).toBeInTheDocument();
    expect(within(tree).getByText("Входит в: Склад клуба")).toBeInTheDocument();
    expect(within(tree).getByText("Старый подвал")).toBeInTheDocument();
    expect(within(tree).queryByText("Стеллаж А")).not.toBeInTheDocument();
    const statuses = requestParams(fetchMock, "/api/v1/inventory/storage-locations").map((params) =>
      params.get("status"),
    );
    expect(statuses).toEqual(["all"]);
  });

  it("formats quantities with Russian digit grouping", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) =>
        path === "/api/v1/inventory/stock"
          ? { body: page([{ item_id: "i-rope", storage_location_id: "l1", quantity: 1234567 }]) }
          : undefined,
      ),
    );
    await renderAt("/inventory?tab=stock");

    const list = await screen.findByRole("list", { name: "Остатки" });
    expect(within(list).getByText(GROUPED_1234567)).toBeInTheDocument();
  });

  it("«← Склад» on an item returns to the tab it was opened from", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        if (path === "/api/v1/inventory/stock") {
          return { body: page([{ item_id: "i-rope", storage_location_id: "l1", quantity: 3 }]) };
        }
        if (path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (path === "/api/v1/inventory/items/i-rope/stock") return { body: page([]) };
        if (path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        return undefined;
      }),
    );
    await renderAt("/inventory?tab=stock");

    const list = await screen.findByRole("list", { name: "Остатки" });
    await userEvent.click(within(list).getByRole("link", { name: "Верёвка 10 мм" }));
    await screen.findByRole("heading", { level: 1, name: "Верёвка 10 мм" });
    const back = screen.getAllByRole("link", { name: "Склад" }).find((link) => !link.closest("nav"));
    expect(back).toHaveAttribute("href", "/inventory?tab=stock");

    await userEvent.click(back as HTMLElement);
    expect(await screen.findByRole("tab", { name: "Остатки", selected: true })).toBeInTheDocument();
  });

  it("an item opened directly returns to «Номенклатура»", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        if (path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (path === "/api/v1/inventory/items/i-rope/stock") return { body: page([]) };
        if (path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    await screen.findByRole("heading", { level: 1, name: "Верёвка 10 мм" });
    const back = screen.getAllByRole("link", { name: "Склад" }).find((link) => !link.closest("nav"));
    expect(back).toHaveAttribute("href", "/inventory");
  });

  it("a missing item shows «не найдено» without a retry", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) =>
        path === "/api/v1/inventory/items/i-gone"
          ? { status: 404, body: { error: { code: "not_found", message: "Inventory item not found" } } }
          : undefined,
      ),
    );
    await renderAt("/inventory/items/i-gone");

    expect(await screen.findByText("Запись не найдена.", {}, RETRIED)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });

  it.each([
    ["/inventory/items/not-a-uuid", "/api/v1/inventory/items/not-a-uuid", "item_id"],
    ["/inventory/instances/not-a-uuid", "/api/v1/inventory/instances/not-a-uuid", "instance_id"],
  ])("an invalid id in %s (422) is shown as «не найдено»", async (route, apiPath, field) => {
    const invalid = {
      status: 422,
      body: {
        error: {
          code: "validation_error",
          message: "Request validation failed",
          details: { fields: [{ field, code: "uuid_parsing", message: "Input should be a valid UUID" }] },
        },
      },
    };
    stubApi(["admin"], inventoryApi((path) => (path.startsWith(apiPath) ? invalid : undefined)));
    await renderAt(route);

    expect(await screen.findByText("Запись не найдена.", {}, RETRIED)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });

  it("a stock error on the item page is shown with a retry, the rest of the page stays", async () => {
    stubApi(
      ["admin"],
      inventoryApi((path) => {
        if (path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (path === "/api/v1/inventory/items/i-rope/stock") {
          return { status: 500, body: { error: { code: "internal_error", message: "Сбой" } } };
        }
        if (path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    expect(await screen.findByText("Не удалось загрузить остатки", {}, RETRIED)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Верёвка 10 мм" })).toBeInTheDocument();
    expect(screen.getByText("Движений пока нет")).toBeInTheDocument();
  });

  it("loads every page of an item's stock instead of cutting it off", async () => {
    const fetchMock = stubApi(
      ["admin"],
      inventoryApi((path, search) => {
        if (path === "/api/v1/inventory/items/i-rope") return { body: ROPE };
        if (path === "/api/v1/inventory/items/i-rope/movements") return { body: page([]) };
        if (path === "/api/v1/inventory/items/i-rope/stock") {
          const pageNumber = Number(search.get("page"));
          const row =
            pageNumber === 1
              ? { item_id: "i-rope", storage_location_id: "l1", quantity: 40 }
              : { item_id: "i-rope", storage_location_id: "l2", quantity: 7 };
          return { body: page([row], pageNumber, 2) };
        }
        return undefined;
      }),
    );
    await renderAt("/inventory/items/i-rope");

    const stock = await screen.findByRole("list", { name: "Остатки по местам хранения" });
    expect(within(stock).getByText("40 м")).toBeInTheDocument();
    expect(within(stock).getByText("7 м")).toBeInTheDocument();
    expect(
      requestParams(fetchMock, "/api/v1/inventory/items/i-rope/stock").map((params) => params.get("page")),
    ).toEqual(["1", "2"]);
  });
});
