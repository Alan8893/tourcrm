import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SettingsNotificationPreferences } from "./SettingsNotificationPreferences";
import { SettingsTelegramLink } from "./SettingsTelegramLink";
import { renderWithProviders } from "../test/renderWithProviders";

const LINK = "/api/v1/me/telegram-link";
const CHALLENGES = "/api/v1/me/telegram-link/challenges";
const PREFERENCES = "/api/v1/me/notification-preferences";
const TOKEN = "Zq3-one_time_token_value_never_rendered_as_text_x";
const DEEP_LINK = `https://t.me/tourcrm_bot?start=${TOKEN}`;

type Reply = { status: number; body?: unknown };
type Request = { method: string; url: string; body: unknown; csrf: string | null };

const OPTIONAL = [
  "event.created",
  "event.updated",
  "registration.created",
  "registration.cancelled",
  "attendance.changed",
  "news.published",
  "achievement.awarded",
];

type BackendOptions = {
  linked?: boolean;
  personalEnabled?: boolean;
  events?: Record<string, boolean>;
  /** Overrides for a request; return undefined to use the default. A
   * thrown error simulates a network failure. */
  override?: (
    method: string,
    url: string,
    body: unknown,
  ) => Reply | undefined | Promise<Reply | undefined>;
};

/** Stateful fake of the two self-scoped APIs: GETs reflect earlier
 * PUT/DELETE, and every request is recorded with its CSRF header. */
function fakeBackend(options: BackendOptions = {}) {
  const state = {
    linked: options.linked ?? false,
    personalEnabled: options.personalEnabled ?? false,
    events: { ...(options.events ?? {}) } as Record<string, boolean>,
  };
  const calls: Request[] = [];
  const preferences = () => ({
    channel: "telegram",
    personal_enabled: state.personalEnabled,
    telegram_linked: state.linked,
    events: [
      ...OPTIONAL.slice(0, 2).map((key) => ({
        event_type: key,
        mandatory: false,
        enabled: state.events[key] === true,
      })),
      { event_type: "event.cancelled", mandatory: true, enabled: true },
      { event_type: "event.rescheduled", mandatory: true, enabled: true },
      ...OPTIONAL.slice(2).map((key) => ({
        event_type: key,
        mandatory: false,
        enabled: state.events[key] === true,
      })),
    ],
  });
  const respond = ({ status, body }: Reply) =>
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body, csrf: new Headers(init?.headers).get("X-CSRF-Token") });
    const custom = await options.override?.(method, url, body);
    if (custom) return respond(custom);
    if (url.endsWith(CHALLENGES) && method === "POST") {
      return respond({
        status: 201,
        body: { deep_link: DEEP_LINK, expires_at: new Date(Date.now() + 15 * 60_000).toISOString() },
      });
    }
    if (url.endsWith(LINK) && method === "GET") {
      return respond({
        status: 200,
        body: { linked: state.linked, linked_at: state.linked ? "2026-10-09T10:00:00Z" : null },
      });
    }
    if (url.endsWith(LINK) && method === "DELETE") {
      state.linked = false;
      return respond({ status: 204 });
    }
    if (url.endsWith(PREFERENCES) && method === "GET") {
      return respond({ status: 200, body: preferences() });
    }
    if (url.endsWith(PREFERENCES) && method === "PUT") {
      const update = body as { personal_enabled?: boolean; events?: Record<string, boolean> };
      if (update.personal_enabled !== undefined) state.personalEnabled = update.personal_enabled;
      Object.assign(state.events, update.events ?? {});
      return respond({ status: 200, body: preferences() });
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, calls, fetchMock };
}

function writes(calls: Request[]) {
  return calls.filter((call) => call.method !== "GET");
}

function renderSection() {
  return renderWithProviders(
    <>
      <SettingsTelegramLink />
      <SettingsNotificationPreferences />
    </>,
  );
}

beforeEach(() => {
  document.cookie = "csrf_token=csrf-123";
});

afterEach(() => {
  document.cookie = "csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  vi.unstubAllGlobals();
});

describe("Personal Telegram link (Issue #343)", () => {
  it("shows the unlinked state and a link action, never a Telegram id field", async () => {
    fakeBackend();
    renderWithProviders(<SettingsTelegramLink />);

    expect(screen.getByText("Загружаем статус Telegram…")).toBeInTheDocument();
    expect(await screen.findByText("Telegram не привязан")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Привязать Telegram" })).toBeEnabled();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("issues a one-time link with CSRF, explains Start and shows the expiry", async () => {
    const { calls } = fakeBackend();
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));

    const open = await screen.findByRole("link", { name: "Открыть Telegram" });
    expect(open).toHaveAttribute("href", DEEP_LINK);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText(/нажмите кнопку «Start»/)).toBeInTheDocument();
    expect(screen.getByText(/Ссылка одноразовая и действует до \d{2}\.\d{2}\.\d{4}/)).toBeInTheDocument();
    // The token is only the href, never visible text.
    expect(document.body.textContent).not.toContain(TOKEN);

    expect(writes(calls)).toEqual([
      { method: "POST", url: CHALLENGES, body: undefined, csrf: "csrf-123" },
    ]);
  });

  it("hides an expired link and offers a new one", async () => {
    fakeBackend({
      override: (method, url) =>
        url.endsWith(CHALLENGES) && method === "POST"
          ? {
              status: 201,
              body: { deep_link: DEEP_LINK, expires_at: new Date(Date.now() + 300).toISOString() },
            }
          : undefined,
    });
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    expect(await screen.findByRole("link", { name: "Открыть Telegram" })).toBeInTheDocument();

    expect(await screen.findByText(/Срок действия ссылки истёк/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Открыть Telegram" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Получить новую ссылку" })).toBeEnabled();
  });

  it("reports a pending link, then switches to linked once the bot completed it", async () => {
    const { state } = fakeBackend();
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    await user.click(await screen.findByRole("button", { name: "Проверить привязку" }));
    expect(await screen.findByText(/Привязка ещё не завершена/)).toBeInTheDocument();

    state.linked = true;
    await user.click(screen.getByRole("button", { name: "Проверить привязку" }));

    expect(await screen.findByRole("button", { name: "Отвязать Telegram" })).toBeInTheDocument();
    // The badge and the success notification.
    expect(screen.getAllByText("Telegram привязан")).toHaveLength(2);
    expect(screen.getByText(/^с \d{2}\.\d{2}\.\d{4}/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Открыть Telegram" })).not.toBeInTheDocument();
  });

  it.each([
    [503, "telegram_linking_unavailable", /бот TourCRM ещё не настроен администратором/],
    [429, "too_many_requests", /Слишком много запросов ссылки/],
    [403, "forbidden", /для вашей учётной записи недоступна/],
    [500, "internal_error", /Не удалось получить ссылку для привязки/],
  ])("shows a safe message for a %s challenge error", async (status, code, message) => {
    fakeBackend({
      override: (method, url) =>
        url.endsWith(CHALLENGES) && method === "POST"
          ? { status, body: { error: { code, message: "raw backend text" } } }
          : undefined,
    });
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("raw backend text")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Открыть Telegram" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Получить новую ссылку" })).toBeEnabled();
  });

  it("shows a network failure and lets the user retry", async () => {
    let fail = true;
    fakeBackend({
      override: (method, url) => {
        if (url.endsWith(CHALLENGES) && method === "POST" && fail) {
          fail = false;
          throw new TypeError("Failed to fetch");
        }
        return undefined;
      },
    });
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Не удалось связаться с сервером/);

    await user.click(screen.getByRole("button", { name: "Получить новую ссылку" }));
    expect(await screen.findByRole("link", { name: "Открыть Telegram" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a status load error with retry", async () => {
    let fail = true;
    fakeBackend({
      override: (method, url) => {
        if (url.endsWith(LINK) && method === "GET" && fail) {
          fail = false;
          return { status: 500, body: { error: { code: "internal_error", message: "x" } } };
        }
        return undefined;
      },
    });
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить статус привязки Telegram.",
    );
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Telegram не привязан")).toBeInTheDocument();
  });

  it("unlinks only after confirmation, with CSRF, and refreshes link and preferences", async () => {
    const { calls } = fakeBackend({ linked: true });
    renderSection();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Отвязать Telegram" }));
    let dialog = screen.getByRole("dialog", { name: "Отвязать Telegram?" });
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(writes(calls)).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Отвязать Telegram" }));
    dialog = screen.getByRole("dialog", { name: "Отвязать Telegram?" });
    expect(dialog).toHaveTextContent(/об отмене и переносе событий/);
    const preferenceReads = calls.filter((c) => c.url.endsWith(PREFERENCES)).length;
    await user.click(within(dialog).getByRole("button", { name: "Отвязать" }));

    expect(await screen.findByText("Telegram не привязан")).toBeInTheDocument();
    expect(await screen.findByText("Telegram отвязан")).toBeInTheDocument();
    expect(writes(calls)).toEqual([{ method: "DELETE", url: LINK, body: undefined, csrf: "csrf-123" }]);
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith(PREFERENCES)).length).toBeGreaterThan(
        preferenceReads,
      ),
    );
    expect(await screen.findByText(/Telegram не привязан: личные сообщения не будут/)).toBeInTheDocument();
  });

  it("keeps the linked state and reports a failed unlink", async () => {
    fakeBackend({
      linked: true,
      override: (method, url) =>
        url.endsWith(LINK) && method === "DELETE"
          ? { status: 500, body: { error: { code: "internal_error", message: "x" } } }
          : undefined,
    });
    renderWithProviders(<SettingsTelegramLink />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Отвязать Telegram" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Отвязать" }),
    );

    expect(await screen.findByText("Не удалось отвязать Telegram. Попробуйте ещё раз.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отвязать Telegram" })).toBeInTheDocument();
  });
});

describe("Personal notification preferences (Issue #343)", () => {
  it("shows unset optional types as OFF and mandatory types read-only with an explanation", async () => {
    fakeBackend({ linked: true });
    renderWithProviders(<SettingsNotificationPreferences />);

    const master = await screen.findByRole("switch", { name: "Личные сообщения в Telegram" });
    expect(master).not.toBeChecked();
    for (const label of [
      "Новое событие для вашей группы",
      "Изменение места события",
      "Ваша регистрация на событие",
      "Отмена вашей регистрации",
      "Изменение отметки посещаемости",
      "Публикация новости",
      "Присвоение достижения",
    ]) {
      expect(screen.getByRole("switch", { name: label })).not.toBeChecked();
    }
    // Mandatory types are never switches.
    expect(screen.getAllByRole("switch")).toHaveLength(8);
    expect(screen.queryByRole("switch", { name: "Отмена события" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Перенос события" })).not.toBeInTheDocument();
    const mandatory = screen.getByRole("region", { name: "Обязательные уведомления" });
    expect(within(mandatory).getByText("Отмена события")).toBeInTheDocument();
    expect(within(mandatory).getByText("Перенос события")).toBeInTheDocument();
    expect(within(mandatory).getAllByText("Обязательное")).toHaveLength(2);
    expect(mandatory).toHaveTextContent(/нельзя отказаться в личных настройках/);
    expect(mandatory).toHaveTextContent(/выключив Telegram для всей системы или отключив правило/);
    // Group/topic posts are not governed here, and there is no Email control.
    expect(screen.getByText(/Публикации в группе и темах клуба/)).toBeInTheDocument();
    expect(screen.queryByText(/email/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/родител|представител/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Telegram не привязан/)).not.toBeInTheDocument();
  });

  it("warns when Telegram is not linked", async () => {
    fakeBackend({ linked: false });
    renderWithProviders(<SettingsNotificationPreferences />);
    expect(
      await screen.findByText(/Telegram не привязан: личные сообщения не будут доставляться/),
    ).toBeInTheDocument();
  });

  it("toggles the master switch alone: per-event choices are preserved and not enabled", async () => {
    const { calls } = fakeBackend({ linked: true, events: { "news.published": true } });
    renderWithProviders(<SettingsNotificationPreferences />);
    const user = userEvent.setup();

    const master = await screen.findByRole("switch", { name: "Личные сообщения в Telegram" });
    await user.click(master);
    await waitFor(() => expect(master).toBeChecked());
    expect(screen.getByRole("switch", { name: "Публикация новости" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Присвоение достижения" })).not.toBeChecked();

    await user.click(master);
    await waitFor(() => expect(master).not.toBeChecked());
    expect(screen.getByRole("switch", { name: "Публикация новости" })).toBeChecked();
    expect(screen.getByText(/Сейчас личные сообщения выключены/)).toBeInTheDocument();

    expect(writes(calls)).toEqual([
      { method: "PUT", url: PREFERENCES, body: { personal_enabled: true }, csrf: "csrf-123" },
      { method: "PUT", url: PREFERENCES, body: { personal_enabled: false }, csrf: "csrf-123" },
    ]);
    expect(await screen.findAllByText("Настройка сохранена")).not.toHaveLength(0);
  });

  it("changes one event type independently", async () => {
    const { calls } = fakeBackend({ linked: true, personalEnabled: true });
    renderWithProviders(<SettingsNotificationPreferences />);
    const user = userEvent.setup();

    const news = await screen.findByRole("switch", { name: "Публикация новости" });
    await user.click(news);
    await waitFor(() => expect(news).toBeChecked());
    expect(screen.getByRole("switch", { name: "Личные сообщения в Telegram" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Присвоение достижения" })).not.toBeChecked();
    expect(writes(calls)).toEqual([
      {
        method: "PUT",
        url: PREFERENCES,
        body: { events: { "news.published": true } },
        csrf: "csrf-123",
      },
    ]);
  });

  it("disables the switches while a change is being saved", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    fakeBackend({
      linked: true,
      override: async (method) => {
        if (method === "PUT") await gate;
        return undefined;
      },
    });
    renderWithProviders(<SettingsNotificationPreferences />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("switch", { name: "Публикация новости" }));
    await waitFor(() =>
      screen.getAllByRole("switch").forEach((control) => expect(control).toBeDisabled()),
    );
    release();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Публикация новости" })).toBeEnabled(),
    );
  });

  it("shows a failed save, keeps the stored value and retries it", async () => {
    let fail = true;
    const { calls } = fakeBackend({
      linked: true,
      override: (method) => {
        if (method === "PUT" && fail) {
          fail = false;
          return { status: 500, body: { error: { code: "internal_error", message: "x" } } };
        }
        return undefined;
      },
    });
    renderWithProviders(<SettingsNotificationPreferences />);
    const user = userEvent.setup();

    const news = await screen.findByRole("switch", { name: "Публикация новости" });
    await user.click(news);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось сохранить настройку. Попробуйте ещё раз.",
    );
    expect(news).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(news).toBeChecked());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(writes(calls).map((call) => call.body)).toEqual([
      { events: { "news.published": true } },
      { events: { "news.published": true } },
    ]);
  });

  it("shows a load error with retry", async () => {
    let fail = true;
    fakeBackend({
      override: (method, url) => {
        if (url.endsWith(PREFERENCES) && method === "GET" && fail) {
          fail = false;
          throw new TypeError("Failed to fetch");
        }
        return undefined;
      },
    });
    renderWithProviders(<SettingsNotificationPreferences />);
    const user = userEvent.setup();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить настройки уведомлений.",
    );
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(
      await screen.findByRole("switch", { name: "Личные сообщения в Telegram" }),
    ).toBeInTheDocument();
  });
});
