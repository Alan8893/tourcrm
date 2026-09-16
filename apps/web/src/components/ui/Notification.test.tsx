import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NotificationProvider } from "./Notification";
import { useNotify } from "./notificationContext";
import { render } from "@testing-library/react";

function NotifyButton() {
  const notify = useNotify();
  return (
    <button type="button" onClick={() => notify("success", "Группа создана")}>
      Уведомить
    </button>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NotificationProvider", () => {
  it("creates notifications when randomUUID is unavailable", async () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(1);
        return bytes;
      },
    });

    render(
      <NotificationProvider>
        <NotifyButton />
      </NotificationProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Уведомить" }));

    expect(screen.getByText("Группа создана")).toBeInTheDocument();
  });
});
