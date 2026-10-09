import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { NotificationSettingsPage } from "./NotificationSettingsPage";
import { SettingsPage } from "./SettingsPage";
import { AdministratorGuard } from "../shell/AdministratorGuard";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi, requests, type MockApiHandler } from "../test/mockApi";

const BASE = "/api/v1/settings/notifications";
const SECRET = "Sm7p-Pa55w0rd-UNIQUE";

function me(roleCodes: string[]) {
  return {
    user: {
      id: "u1",
      login_identifier: "admin@club.test",
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
    role_assignments: roleCodes.map((role_code) => ({
      role_code,
      club_id: "club-1",
      scope_type: "all",
    })),
  };
}

const POLICY = { email_enabled: false, telegram_enabled: false, saved: false };
const STATUS = {
  encryption: "available",
  email: { policy_enabled: false, configuration: "not_configured", ready: false },
  telegram: {
    policy_enabled: false,
    configuration: "not_configured",
    ready: false,
    linking_available: false,
  },
};
const EMPTY = { items: [], pagination: { page: 1, page_size: 1, total: 0, pages: 0 } };

function integrations(overrides: { password?: boolean; token?: boolean; encryption?: string } = {}) {
  return {
    encryption: overrides.encryption ?? "available",
    email: {
      smtp_host: "smtp.club.test",
      smtp_port: 587,
      smtp_security: "starttls",
      smtp_username: "mailer",
      sender_email: "noreply@club.test",
      sender_name: "Клуб",
      password_configured: overrides.password ?? true,
    },
    telegram: { bot_username: "club_bot", bot_token_configured: overrides.token ?? false },
  };
}

function handlers(extra: MockApiHandler[] = [], overrides: Partial<Record<string, unknown>> = {}) {
  return [
    ...extra,
    { method: "GET", match: "/auth/me", body: me(["admin"]) },
    { method: "GET", match: `${BASE}/policy`, body: overrides.policy ?? POLICY },
    { method: "GET", match: `${BASE}/status`, body: overrides.status ?? STATUS },
    { method: "GET", match: `${BASE}/rules`, body: overrides.rules ?? EMPTY },
    { method: "GET", match: `${BASE}/integrations`, body: overrides.integrations ?? integrations() },
    { method: "GET", match: `${BASE}/telegram-destinations`, body: overrides.destinations ?? EMPTY },
  ] as MockApiHandler[];
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route element={<AdministratorGuard />}>
        <Route path="/settings/notifications" element={<NotificationSettingsPage />} />
      </Route>
    </Routes>,
    { route: "/settings/notifications" },
  );
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, method: string, path: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([input, init]) =>
      String(input).endsWith(path) && (init?.method ?? "GET").toUpperCase() === method,
  );
  return call ? JSON.parse(String(call[1]?.body ?? "null")) : undefined;
}

function card(title: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: title });
  return heading.parentElement as HTMLElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Settings → Notifications visibility", () => {
  it("links administrators from Settings and hides the link from other roles", async () => {
    mockApi([
      { method: "GET", match: "/auth/me", body: me(["admin"]) },
      { method: "GET", match: "/persons/p1", status: 404, body: { error: { code: "x" } } },
    ]);
    renderWithProviders(<SettingsPage />, { route: "/settings" });
    expect(await screen.findByRole("link", { name: "Уведомления" })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });

  it.each(["member", "instructor", "guardian"])("hides the link from %s", async (role) => {
    mockApi([
      { method: "GET", match: "/auth/me", body: me([role]) },
      { method: "GET", match: "/persons/p1", status: 404, body: { error: { code: "x" } } },
    ]);
    renderWithProviders(<SettingsPage />, { route: "/settings" });
    await screen.findByRole("heading", { name: "Безопасность" });
    expect(screen.queryByRole("link", { name: "Уведомления" })).not.toBeInTheDocument();
  });

  it("shows the standard 403 state to non-administrators by direct URL", async () => {
    const fetchMock = mockApi([{ method: "GET", match: "/auth/me", body: me(["instructor"]) }]);
    renderPage();
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Уведомления" })).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(requests(fetchMock).some(([, url]) => url.includes(BASE))).toBe(false);
  });
});

describe("Global policy and rules", () => {
  it("explains the default OFF policy and saves a toggle", async () => {
    const fetchMock = mockApi(
      handlers([
        {
          method: "PUT",
          match: `${BASE}/policy`,
          body: { email_enabled: true, telegram_enabled: false, saved: true },
        },
      ]),
    );
    renderPage();
    expect(await screen.findByText(/Политика ещё не сохранялась/)).toBeInTheDocument();
    const general = card("Общие");
    await userEvent.click(within(general).getByRole("switch", { name: "Email" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "PUT", "/settings/notifications/policy")).toEqual({
        email_enabled: true,
        telegram_enabled: false,
      }),
    );
  });

  it("shows readiness per channel", async () => {
    mockApi(
      handlers([], {
        status: {
          ...STATUS,
          email: { policy_enabled: true, configuration: "configured", ready: true },
          telegram: {
            policy_enabled: true,
            configuration: "secret_unavailable",
            ready: false,
            linking_available: true,
          },
        },
        policy: { email_enabled: true, telegram_enabled: true, saved: true },
      }),
    );
    renderPage();
    const general = await waitFor(() => {
      const element = card("Общие");
      expect(within(element).getByText("Готов к отправке")).toBeInTheDocument();
      return element;
    });
    expect(within(general).getByText("Секрет недоступен")).toBeInTheDocument();
  });

  it("shows an empty rule list as a normal state and toggles an existing rule", async () => {
    const fetchMock = mockApi(
      handlers(
        [
          {
            method: "PATCH",
            match: `${BASE}/rules/r1`,
            body: {
              id: "r1",
              event_type: "event.cancelled",
              channel: "email",
              recipient_scope: "participants",
              is_enabled: false,
            },
          },
        ],
        {
          rules: {
            items: [
              {
                id: "r1",
                event_type: "event.cancelled",
                channel: "email",
                recipient_scope: "participants",
                is_enabled: true,
              },
            ],
            pagination: { page: 1, page_size: 1, total: 1, pages: 1 },
          },
        },
      ),
    );
    renderPage();
    const rule = await screen.findByRole("switch", { name: /event\.cancelled/ });
    expect(rule).toBeChecked();
    await userEvent.click(rule);
    await waitFor(() =>
      expect(bodyOf(fetchMock, "PATCH", "/settings/notifications/rules/r1")).toEqual({
        is_enabled: false,
      }),
    );
    // No create/delete controls.
    expect(screen.queryByRole("button", { name: /Добавить правило|Удалить правило/ })).toBeNull();
  });

  it("renders the empty rules note", async () => {
    mockApi(handlers());
    renderPage();
    expect(await screen.findByText(/Правил пока нет/)).toBeInTheDocument();
  });
});

describe("Integrations and write-only secrets", () => {
  it("masks a configured secret and never requests or shows its value", async () => {
    const fetchMock = mockApi(handlers());
    renderPage();
    const password = await screen.findByTestId("secret-smtp_password");
    expect(within(password).getByText("••••••••")).toBeInTheDocument();
    expect(within(password).getByText("Настроен")).toBeInTheDocument();
    expect(within(password).getByRole("button", { name: "Заменить" })).toBeInTheDocument();
    // No input holds or could display the stored value.
    expect(password.querySelector("input")).toBeNull();
    const token = screen.getByTestId("secret-telegram_bot_token");
    expect(within(token).getByText("Не настроен")).toBeInTheDocument();
    expect(within(token).getByRole("button", { name: "Задать" })).toBeInTheDocument();
    expect(within(token).queryByRole("button", { name: "Очистить" })).toBeNull();
    // Only GETs were made; no secret endpoint was read.
    expect(requests(fetchMock).every(([method]) => method === "GET")).toBe(true);
  });

  it("saves non-secret settings without touching the secret (unchanged)", async () => {
    const fetchMock = mockApi(
      handlers([{ method: "PUT", match: `${BASE}/integrations/email`, body: integrations().email }]),
    );
    renderPage();
    const host = await screen.findByLabelText("SMTP-сервер");
    await userEvent.clear(host);
    await userEvent.type(host, "smtp2.club.test");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить настройки Email" }));
    expect(await screen.findByText("Настройки Email сохранены.")).toBeInTheDocument();
    const body = bodyOf(fetchMock, "PUT", "/settings/notifications/integrations/email") as Record<
      string,
      unknown
    >;
    expect(body.smtp_host).toBe("smtp2.club.test");
    expect(Object.keys(body)).not.toContain("password");
    expect(JSON.stringify(body)).not.toContain("••••••••");
    expect(requests(fetchMock).some(([, url]) => url.endsWith("/email/password"))).toBe(false);
  });

  it("replaces a secret only through the explicit action and clears the input", async () => {
    const fetchMock = mockApi(
      handlers([
        { method: "PUT", match: `${BASE}/integrations/email/password`, body: { configured: true } },
      ]),
    );
    renderPage();
    const password = await screen.findByTestId("secret-smtp_password");
    await userEvent.click(within(password).getByRole("button", { name: "Заменить" }));
    const input = within(password).getByLabelText("Новое значение: Пароль SMTP");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("");
    await userEvent.type(input, SECRET);
    await userEvent.click(within(password).getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "PUT", "/settings/notifications/integrations/email/password")).toEqual(
        { value: SECRET },
      ),
    );
    await waitFor(() => expect(within(password).queryByLabelText(/Новое значение/)).toBeNull());
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it("cancelling a replacement sends nothing", async () => {
    const fetchMock = mockApi(handlers());
    renderPage();
    const password = await screen.findByTestId("secret-smtp_password");
    await userEvent.click(within(password).getByRole("button", { name: "Заменить" }));
    await userEvent.type(within(password).getByLabelText(/Новое значение/), SECRET);
    await userEvent.click(within(password).getByRole("button", { name: "Отмена" }));
    expect(within(password).getByText("••••••••")).toBeInTheDocument();
    expect(requests(fetchMock).filter(([method]) => method !== "GET")).toEqual([]);
  });

  it("clears a secret only after confirmation", async () => {
    const fetchMock = mockApi(
      handlers([{ method: "DELETE", match: `${BASE}/integrations/email/password`, status: 204 }]),
    );
    renderPage();
    const password = await screen.findByTestId("secret-smtp_password");
    await userEvent.click(within(password).getByRole("button", { name: "Очистить" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(requests(fetchMock).some(([method]) => method === "DELETE")).toBe(false);

    await userEvent.click(within(password).getByRole("button", { name: "Очистить" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Очистить",
    }));
    await waitFor(() =>
      expect(
        requests(fetchMock).filter(([method]) => method === "DELETE").map(([, url]) => url),
      ).toEqual([`${BASE}/integrations/email/password`]),
    );
  });

  it("shows a safe message when the server cannot encrypt secrets", async () => {
    mockApi(
      handlers(
        [
          {
            method: "PUT",
            match: `${BASE}/integrations/telegram/bot-token`,
            status: 503,
            body: { error: { code: "settings_encryption_unavailable", message: "x" } },
          },
        ],
        { status: { ...STATUS, encryption: "missing" } },
      ),
    );
    renderPage();
    expect(await screen.findByText(/Ключ шифрования настроек не задан/)).toBeInTheDocument();
  });

  it("disables secret actions when encryption is unavailable", async () => {
    mockApi(handlers([], { integrations: integrations({ encryption: "invalid" }) }));
    renderPage();
    const password = await screen.findByTestId("secret-smtp_password");
    expect(within(password).getByRole("button", { name: "Заменить" })).toBeDisabled();
    expect(within(password).getByRole("button", { name: "Очистить" })).toBeDisabled();
  });

  it("maps validation errors to field messages", async () => {
    mockApi(
      handlers([
        {
          method: "PUT",
          match: `${BASE}/integrations/email`,
          status: 422,
          body: { error: { code: "invalid_sender_email", message: "raw backend text" } },
        },
      ]),
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Сохранить настройки Email" }),
    );
    expect(await screen.findByText("Укажите корректный адрес отправителя.")).toBeInTheDocument();
    expect(screen.queryByText("raw backend text")).toBeNull();
  });

  it("shows a note when the integrations section is not permitted", async () => {
    mockApi(
      handlers([
        {
          method: "GET",
          match: `${BASE}/integrations`,
          status: 403,
          body: { error: { code: "forbidden", message: "x" } },
        },
      ]),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText("Этот раздел вам недоступен.")).toHaveLength(2),
    );
  });

  it("shows loading, then a retryable error state", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi(
      handlers([
        {
          method: "GET",
          match: `${BASE}/rules`,
          status: 500,
          body: { error: { code: "internal_error", message: "x" } },
          gate: () => gate,
        },
      ]),
    );
    renderPage();
    expect(await screen.findByText("Загружаем правила…")).toBeInTheDocument();
    release();
    const rules = await waitFor(() => {
      const element = card("Правила уведомлений");
      expect(within(element).getByText("Не удалось загрузить данные.")).toBeInTheDocument();
      return element;
    });
    expect(within(rules).getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });
});

describe("Test send", () => {
  it("sends an Email test and labels the result as a test", async () => {
    const fetchMock = mockApi(
      handlers([
        {
          method: "POST",
          match: `${BASE}/test-send`,
          body: {
            test: true,
            channel: "email",
            destination_kind: "email_address",
            status: "delivered",
            error_code: null,
          },
        },
      ]),
    );
    renderPage();
    const section = await waitFor(() => card("Тестовое сообщение"));
    await userEvent.type(within(section).getByLabelText("Адрес получателя"), "me@club.test");
    await userEvent.click(within(section).getByRole("button", { name: "Отправить тестовое сообщение" }));
    expect(await within(section).findByText("Тест: отправлено")).toBeInTheDocument();
    expect(bodyOf(fetchMock, "POST", "/settings/notifications/test-send")).toEqual({
      channel: "email",
      destination_kind: "email_address",
      email: "me@club.test",
    });
  });

  it("shows a safe message for a failed test", async () => {
    mockApi(
      handlers([
        {
          method: "POST",
          match: `${BASE}/test-send`,
          body: {
            test: true,
            channel: "email",
            destination_kind: "email_address",
            status: "failed",
            error_code: "smtp_authentication_failed",
          },
        },
      ]),
    );
    renderPage();
    const section = await waitFor(() => card("Тестовое сообщение"));
    await userEvent.type(within(section).getByLabelText("Адрес получателя"), "me@club.test");
    await userEvent.click(within(section).getByRole("button", { name: "Отправить тестовое сообщение" }));
    expect(await within(section).findByText("Тест: ошибка")).toBeInTheDocument();
    expect(
      within(section).getByText("SMTP-сервер отклонил имя пользователя или пароль."),
    ).toBeInTheDocument();
  });

  it("explains the rate limit", async () => {
    mockApi(
      handlers([
        {
          method: "POST",
          match: `${BASE}/test-send`,
          status: 429,
          body: { error: { code: "too_many_requests", message: "x" } },
        },
      ]),
    );
    renderPage();
    const section = await waitFor(() => card("Тестовое сообщение"));
    await userEvent.type(within(section).getByLabelText("Адрес получателя"), "me@club.test");
    await userEvent.click(within(section).getByRole("button", { name: "Отправить тестовое сообщение" }));
    expect(await within(section).findByText(/Слишком много тестовых сообщений/)).toBeInTheDocument();
  });

  it("offers only the own account or existing destinations for Telegram", async () => {
    const fetchMock = mockApi(
      handlers(
        [
          {
            method: "POST",
            match: `${BASE}/test-send`,
            body: {
              test: true,
              channel: "telegram",
              destination_kind: "telegram_destination",
              status: "delivered",
              error_code: null,
            },
          },
        ],
        {
          destinations: {
            items: [{ id: "d1", name: "Клуб", topic_name: "Походы" }],
            pagination: { page: 1, page_size: 1, total: 1, pages: 1 },
          },
        },
      ),
    );
    renderPage();
    const section = await waitFor(() => card("Тестовое сообщение"));
    await userEvent.selectOptions(within(section).getByLabelText("Канал"), "telegram");
    const target = within(section).getByLabelText("Получатель");
    expect(within(target).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Мой Telegram-аккаунт",
      "Клуб — Походы",
    ]);
    // No free-form chat id input.
    expect(within(section).queryByLabelText(/chat/i)).toBeNull();
    await userEvent.selectOptions(target, "d1");
    await userEvent.click(within(section).getByRole("button", { name: "Отправить тестовое сообщение" }));
    await waitFor(() =>
      expect(bodyOf(fetchMock, "POST", "/settings/notifications/test-send")).toEqual({
        channel: "telegram",
        destination_kind: "telegram_destination",
        telegram_destination_id: "d1",
      }),
    );
  });
});
