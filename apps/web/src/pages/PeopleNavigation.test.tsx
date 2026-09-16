import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { PeoplePage } from "./PeoplePage";
import { PersonDetailPage } from "./PersonDetailPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

function emptyCollection() {
  return { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("People list -> detail -> back navigation", () => {
  it("opens a person from the list and returns to the list via the back link", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: {
          items: [
            {
              id: "p1",
              first_name: "Анна",
              last_name: "Иванова",
              middle_name: null,
              birth_date: null,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
          pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
        },
      },
      { match: "/persons/p1/memberships", response: emptyCollection() },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      {
        match: "/persons/p1",
        response: {
          id: "p1",
          first_name: "Анна",
          last_name: "Иванова",
          middle_name: null,
          birth_date: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Иванова Анна" }));

    expect(await screen.findByRole("heading", { name: "Иванова Анна" })).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: /Все люди/ }));

    expect(await screen.findByRole("heading", { name: "Люди" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Иванова Анна" })).toBeInTheDocument();
  });
});
