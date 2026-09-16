import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { LoginPage } from "./LoginPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

/** `LoginPage` calls `navigate()` on success/already-authenticated — a
 * bare `renderWithProviders(<LoginPage />)` has no route tree to react to
 * that, so it would stay mounted regardless. Routing tests render this
 * instead, with simple marker pages standing in for the real destinations. */
function LoginRoutesHarness() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<div>HOME PAGE</div>} />
      <Route path="/groups" element={<div>GROUPS PAGE</div>} />
    </Routes>
  );
}

const ME_RESPONSE = {
  user: {
    id: "u1",
    login_identifier: "admin@example.com",
    status: "active",
    email_verified_at: null,
    person: { first_name: "Admin", last_name: "Admin", middle_name: null, birth_date: null },
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  it("renders an accessible login form when signed out", async () => {
    stubFetch([{ match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 }]);

    renderWithProviders(<LoginPage />);

    expect(await screen.findByRole("heading", { name: "Вход в TourCRM" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email или логин")).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Войти" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Забыли пароль?" })).toBeInTheDocument();
  });

  it("redirects an already-authenticated visitor to Home instead of showing the login form", async () => {
    stubFetch([{ match: "/auth/me", response: ME_RESPONSE }]);

    renderWithProviders(<LoginRoutesHarness />, { route: "/login" });

    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Вход в TourCRM" })).not.toBeInTheDocument();
  });

  it("logs in successfully and resolves /auth/me", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      { match: "/auth/login", response: ME_RESPONSE },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginPage />);
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    await waitFor(() => {
      const loginCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).includes("/auth/login"),
      );
      expect(loginCall).toBeDefined();
    });
  });

  it("shows a generic failure message on invalid credentials, without revealing account state", async () => {
    stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      {
        match: "/auth/login",
        response: { error: { code: "invalid_credentials", message: "Invalid credentials" } },
        status: 401,
      },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginPage />);
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось войти. Проверьте логин и пароль.");
    expect(alert.textContent).not.toMatch(/existe|найден|pending|заблокирован/i);
  });

  it("shows a network/server error without exposing raw backend detail, on a 500", async () => {
    stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      {
        match: "/auth/login",
        response: { error: { code: "internal_error", message: "Traceback (most recent call)" } },
        status: 500,
      },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginPage />);
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "whatever12");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось подключиться к серверу. Попробуйте ещё раз.");
    expect(alert.textContent).not.toContain("Traceback");
  });

  it("prevents a duplicate submit while a login request is in flight", async () => {
    let resolveLogin: (() => void) | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/auth/me")) {
        return new Response(JSON.stringify({ error: { code: "unauthorized" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/auth/login")) {
        await new Promise<void>((resolve) => {
          resolveLogin = resolve;
        });
        return new Response(JSON.stringify(ME_RESPONSE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`No stub registered for fetch(${url})`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<LoginPage />);
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "correct-password");
    const submitButton = screen.getByRole("button", { name: "Войти" });
    await user.click(submitButton);
    await waitFor(() => expect(submitButton).toBeDisabled());
    await user.click(submitButton);

    resolveLogin?.();

    await waitFor(() => {
      const loginCalls = fetchMock.mock.calls.filter((call) =>
        String(call[0]).includes("/auth/login"),
      );
      expect(loginCalls).toHaveLength(1);
    });
  });

  it("redirects to a safe internal ?next= target after login", async () => {
    stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      { match: "/auth/login", response: ME_RESPONSE },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginRoutesHarness />, { route: "/login?next=/groups" });
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("GROUPS PAGE")).toBeInTheDocument();
  });

  it("ignores an external ?next= target and falls back to Home", async () => {
    stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      { match: "/auth/login", response: ME_RESPONSE },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginRoutesHarness />, {
      route: "/login?next=https://evil.example/phish",
    });
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument();
  });

  it("never writes credentials into localStorage or sessionStorage", async () => {
    stubFetch([
      { match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 },
      { match: "/auth/login", response: ME_RESPONSE },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<LoginRoutesHarness />, { route: "/login" });
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    await user.type(screen.getByLabelText("Email или логин"), "admin@example.com");
    await user.type(screen.getByLabelText("Пароль"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    await screen.findByText("HOME PAGE");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("toggles password visibility via an accessible, text-only control", async () => {
    stubFetch([{ match: "/auth/me", response: { error: { code: "unauthorized" } }, status: 401 }]);
    const user = userEvent.setup();

    renderWithProviders(<LoginPage />);
    await screen.findByRole("heading", { name: "Вход в TourCRM" });

    const passwordInput = screen.getByLabelText("Пароль") as HTMLInputElement;
    expect(passwordInput.type).toBe("password");

    await user.click(screen.getByRole("button", { name: "Показать пароль" }));
    expect(passwordInput.type).toBe("text");

    await user.click(screen.getByRole("button", { name: "Скрыть пароль" }));
    expect(passwordInput.type).toBe("password");
  });
});
