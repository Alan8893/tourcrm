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
