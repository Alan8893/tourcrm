import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PasswordResetRequestPage } from "./PasswordResetRequestPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PasswordResetRequestPage", () => {
  it("shows the same generic confirmation regardless of whether the account exists", async () => {
    stubFetch([{ match: "/auth/password-reset/request", response: { status: "ok" } }]);
    const user = userEvent.setup();

    renderWithProviders(<PasswordResetRequestPage />);

    await user.type(screen.getByLabelText("Email или логин"), "someone@example.com");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(
      await screen.findByText(
        "Если такая учётная запись существует, на связанный с ней email отправлены инструкции по восстановлению пароля.",
      ),
    ).toBeInTheDocument();
  });

  it("shows the identical confirmation even when the backend call fails, never revealing account state", async () => {
    stubFetch([
      {
        match: "/auth/password-reset/request",
        response: { error: { code: "rate_limited" } },
        status: 429,
      },
    ]);
    const user = userEvent.setup();

    renderWithProviders(<PasswordResetRequestPage />);

    await user.type(screen.getByLabelText("Email или логин"), "someone@example.com");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(
      await screen.findByText(
        "Если такая учётная запись существует, на связанный с ней email отправлены инструкции по восстановлению пароля.",
      ),
    ).toBeInTheDocument();
  });

  it("links back to the login page", () => {
    renderWithProviders(<PasswordResetRequestPage />);
    expect(screen.getByRole("link", { name: "Вернуться ко входу" })).toBeInTheDocument();
  });
});

describe("PasswordResetRequestPage — confirm/setup step (TH-0113)", () => {
  it("switches to the confirm step and back via the toggle links", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PasswordResetRequestPage />);

    await user.click(screen.getByRole("button", { name: "У меня уже есть одноразовый код" }));
    expect(screen.getByRole("heading", { name: "Ввести код и задать пароль" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "У меня нет кода — запросить сброс пароля" }));
    expect(screen.getByRole("heading", { name: "Восстановление пароля" })).toBeInTheDocument();
  });

  it("submits the one-time code and new password, and shows success", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/password-reset/confirm", response: { status: "ok" } },
    ]);
    const user = userEvent.setup();
    renderWithProviders(<PasswordResetRequestPage />);

    await user.click(screen.getByRole("button", { name: "У меня уже есть одноразовый код" }));
    await user.type(screen.getByLabelText("Одноразовый код"), "one-time-code-123");
    await user.type(screen.getByLabelText("Новый пароль"), "brandnewpassword1");
    await user.type(screen.getByLabelText("Повторите пароль"), "brandnewpassword1");
    await user.click(screen.getByRole("button", { name: "Установить пароль" }));

    expect(await screen.findByText("Ваш новый пароль сохранён. Теперь вы можете войти с его помощью.")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/auth/password-reset/confirm"),
    );
    expect(call).toBeDefined();
    const body = JSON.parse(String((call?.[1] as RequestInit).body));
    expect(body).toEqual({ token: "one-time-code-123", new_password: "brandnewpassword1" });
  });

  it("shows a client-side error when the passwords do not match, without submitting", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/password-reset/confirm", response: { status: "ok" } },
    ]);
    const user = userEvent.setup();
    renderWithProviders(<PasswordResetRequestPage />);

    await user.click(screen.getByRole("button", { name: "У меня уже есть одноразовый код" }));
    await user.type(screen.getByLabelText("Одноразовый код"), "code");
    await user.type(screen.getByLabelText("Новый пароль"), "brandnewpassword1");
    await user.type(screen.getByLabelText("Повторите пароль"), "somethingelse1");
    await user.click(screen.getByRole("button", { name: "Установить пароль" }));

    expect(await screen.findByText("Пароли не совпадают.")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/auth/password-reset/confirm")),
    ).toBe(false);
  });

  it("shows the backend error for an invalid or expired code", async () => {
    stubFetch([
      {
        match: "/auth/password-reset/confirm",
        response: {
          error: {
            code: "invalid_or_expired_token",
            message: "Код недействителен или истёк",
            details: {},
            request_id: "r1",
          },
        },
        status: 400,
      },
    ]);
    const user = userEvent.setup();
    renderWithProviders(<PasswordResetRequestPage />);

    await user.click(screen.getByRole("button", { name: "У меня уже есть одноразовый код" }));
    await user.type(screen.getByLabelText("Одноразовый код"), "expired-code");
    await user.type(screen.getByLabelText("Новый пароль"), "brandnewpassword1");
    await user.type(screen.getByLabelText("Повторите пароль"), "brandnewpassword1");
    await user.click(screen.getByRole("button", { name: "Установить пароль" }));

    expect(await screen.findByText("Код недействителен или истёк")).toBeInTheDocument();
    expect(
      screen.queryByText("Ваш новый пароль сохранён. Теперь вы можете войти с его помощью."),
    ).not.toBeInTheDocument();
  });
});
