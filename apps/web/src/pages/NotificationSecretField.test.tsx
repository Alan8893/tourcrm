import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";

import { NotificationSecretField } from "./NotificationSecretField";
import { createTestQueryClient, renderWithProviders } from "../test/renderWithProviders";
import { mockApi, type MockApiHandler } from "../test/mockApi";

// Test-only placeholder values — never a real credential.
const FIRST_VALUE = "test-only-value-one";
const SECOND_VALUE = "test-only-value-two";
const PATH = "/api/v1/settings/notifications/integrations/email/password";

function renderField(handlers: MockApiHandler[], configured = true) {
  const fetchMock = mockApi([
    ...handlers,
    // Invalidation after success refetches nothing in this isolated render.
    { method: "GET", match: "/settings/notifications", body: {} },
  ]);
  const client = createTestQueryClient();
  renderWithProviders(
    <NotificationSecretField name="smtp_password" label="Пароль SMTP" configured={configured} />,
    { client },
  );
  return { fetchMock, client, field: screen.getByTestId("secret-smtp_password") };
}

/** Every variables value still held by the MutationCache. */
function cachedVariables(client: QueryClient): unknown[] {
  return client
    .getMutationCache()
    .getAll()
    .map((mutation) => mutation.state.variables);
}

function sentValues(fetchMock: ReturnType<typeof mockApi>): unknown[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => String(input).endsWith(PATH) && init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init?.body)).value);
}

function secretInput(field: HTMLElement): HTMLInputElement | null {
  return field.querySelector('input[type="password"]');
}

async function enterAndSave(field: HTMLElement, value: string) {
  const input = within(field).getByLabelText(/Пароль SMTP$/);
  await userEvent.type(input, value);
  await userEvent.click(within(field).getByRole("button", { name: "Сохранить" }));
}

function expectNoTrace(client: QueryClient, ...values: string[]) {
  for (const value of values) {
    expect(document.body.textContent).not.toContain(value);
    expect(cachedVariables(client)).not.toContain(value);
    for (const input of document.querySelectorAll("input")) {
      expect((input as HTMLInputElement).value).not.toBe(value);
    }
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NotificationSecretField", () => {
  it("clears the value and the mutation cache after a successful save", async () => {
    const { fetchMock, client, field } = renderField([
      { method: "PUT", match: PATH, body: { configured: true } },
    ]);
    await userEvent.click(within(field).getByRole("button", { name: "Заменить" }));
    await enterAndSave(field, FIRST_VALUE);

    // Sent exactly once, before anything was cleared.
    await waitFor(() => expect(sentValues(fetchMock)).toEqual([FIRST_VALUE]));
    await waitFor(() => expect(secretInput(field)).toBeNull());
    expect(within(field).getByText("••••••••")).toBeInTheDocument();
    expect(await screen.findByText("Пароль SMTP: значение заменено")).toBeInTheDocument();
    expectNoTrace(client, FIRST_VALUE);
  });

  it("clears the value after an HTTP error, shows a safe message and allows a retry", async () => {
    let calls = 0;
    const { fetchMock, client, field } = renderField([
      {
        method: "PUT",
        match: PATH,
        status: 422,
        body: () => {
          calls += 1;
          return { error: { code: "invalid_secret", message: `rejected ${FIRST_VALUE}` } };
        },
      },
    ]);
    await userEvent.click(within(field).getByRole("button", { name: "Заменить" }));
    await enterAndSave(field, FIRST_VALUE);

    expect(
      await within(field).findByText("Значение не подходит. Проверьте его и введите заново."),
    ).toBeInTheDocument();
    const input = within(field).getByLabelText(/Пароль SMTP$/);
    expect(input).toHaveValue("");
    // The raw server message (which even echoes the value) is never shown.
    expectNoTrace(client, FIRST_VALUE);
    expect(calls).toBe(1);

    // Still usable: a new value is sent with the next attempt.
    await enterAndSave(field, SECOND_VALUE);
    await waitFor(() => expect(sentValues(fetchMock)).toEqual([FIRST_VALUE, SECOND_VALUE]));
    await waitFor(() => expect(within(field).getByLabelText(/Пароль SMTP$/)).toHaveValue(""));
    expectNoTrace(client, FIRST_VALUE, SECOND_VALUE);
  });

  it("clears the value after a network error", async () => {
    const { client, field } = renderField([
      {
        method: "PUT",
        match: PATH,
        body: () => {
          throw new TypeError("Failed to fetch");
        },
      },
    ]);
    await userEvent.click(within(field).getByRole("button", { name: "Заменить" }));
    await enterAndSave(field, FIRST_VALUE);

    expect(await within(field).findByText(/Не удалось связаться с сервером/)).toBeInTheDocument();
    expect(within(field).getByLabelText(/Пароль SMTP$/)).toHaveValue("");
    expectNoTrace(client, FIRST_VALUE);
  });

  it("clears the value after a server-side encryption failure", async () => {
    const { client, field } = renderField(
      [
        {
          method: "PUT",
          match: PATH,
          status: 503,
          body: { error: { code: "settings_encryption_unavailable", message: "x" } },
        },
      ],
      false,
    );
    await userEvent.click(within(field).getByRole("button", { name: "Задать" }));
    await enterAndSave(field, FIRST_VALUE);
    expect(
      await within(field).findByText("Секрет не сохранён: на сервере не настроен ключ шифрования."),
    ).toBeInTheDocument();
    expectNoTrace(client, FIRST_VALUE);
  });

  it("keeps the value only while the request is in flight", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { client, field } = renderField([
      { method: "PUT", match: PATH, body: { configured: true }, gate: () => gate },
    ]);
    await userEvent.click(within(field).getByRole("button", { name: "Заменить" }));
    await enterAndSave(field, FIRST_VALUE);
    // Not lost before it was sent: the field is disabled, not emptied.
    expect(within(field).getByLabelText(/Пароль SMTP$/)).toBeDisabled();
    expect(within(field).getByRole("button", { name: "Сохранение…" })).toBeDisabled();
    release();
    await waitFor(() => expect(secretInput(field)).toBeNull());
    expectNoTrace(client, FIRST_VALUE);
  });

  it("does not touch unrelated mutations in the cache", async () => {
    const { client, field } = renderField([
      { method: "PUT", match: PATH, body: { configured: true } },
    ]);
    const unrelated = client.getMutationCache().build(client, {
      mutationKey: ["unrelated"],
      mutationFn: async (variables: string) => variables,
    });
    await unrelated.execute("keep-me");

    await userEvent.click(within(field).getByRole("button", { name: "Заменить" }));
    await enterAndSave(field, FIRST_VALUE);
    await waitFor(() => expect(secretInput(field)).toBeNull());
    expect(cachedVariables(client)).toEqual(["keep-me"]);
  });
});
