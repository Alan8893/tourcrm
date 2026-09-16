import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { PersonDetailPage } from "./PersonDetailPage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

const PERSON = {
  id: "p1",
  first_name: "Анна",
  last_name: "Иванова",
  middle_name: "Сергеевна",
  birth_date: "2012-05-01",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const GUARDIAN_PERSON = {
  id: "g1",
  first_name: "Ольга",
  last_name: "Иванова",
  middle_name: null,
  birth_date: null,
  created_at: "2020-01-01T00:00:00Z",
  updated_at: "2020-01-01T00:00:00Z",
};

function emptyCollection() {
  return { items: [], pagination: { page: 1, page_size: 50, total: 0, pages: 0 } };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PersonDetailPage", () => {
  it("shows identity and a back link, and renders membership periods on the Членство tab", async () => {
    stubFetch([
      {
        match: "/persons/p1/memberships",
        response: {
          items: [
            {
              id: "m1",
              club_id: "club-1",
              person_id: "p1",
              membership_type: "student",
              status: "active",
              joined_at: "2025-09-01T00:00:00Z",
              left_at: null,
              created_at: "2025-09-01T00:00:00Z",
              updated_at: "2025-09-01T00:00:00Z",
            },
          ],
          pagination: { page: 1, page_size: 50, total: 1, pages: 1 },
        },
      },
      { match: "/persons/p1/guardian-relationships", response: emptyCollection() },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    expect(await screen.findByRole("heading", { name: "Иванова Анна Сергеевна" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Все люди/ })).toHaveAttribute("href", "/people");

    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Членство" }));

    expect(await screen.findByText("student")).toBeInTheDocument();
    expect(screen.getByText("Активно")).toBeInTheDocument();
  });

  it("resolves and renders guardian relationships on the Представители tab", async () => {
    stubFetch([
      { match: "/persons/p1/memberships", response: emptyCollection() },
      {
        match: "/persons/p1/guardian-relationships",
        response: {
          items: [
            {
              id: "gr1",
              guardian_person_id: "g1",
              child_person_id: "p1",
              relationship_type: "parent",
              status: "active",
              is_primary_contact: true,
              valid_from: "2020-01-01T00:00:00Z",
              valid_to: null,
              created_at: "2020-01-01T00:00:00Z",
              updated_at: "2020-01-01T00:00:00Z",
            },
          ],
          pagination: { page: 1, page_size: 50, total: 1, pages: 1 },
        },
      },
      { match: "/persons/g1", response: GUARDIAN_PERSON },
      { match: "/persons/p1", response: PERSON },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Представители" }));

    expect(await screen.findByText("Иванова Ольга")).toBeInTheDocument();
    expect(screen.getByText(/parent/)).toBeInTheDocument();
    expect(screen.getByText("Основной контакт")).toBeInTheDocument();
  });

  it("shows a not-found state for a person that does not exist or is not visible", async () => {
    stubFetch([
      {
        match: "/persons/missing",
        response: { error: { code: "not_found", message: "Person not found", details: {}, request_id: "r1" } },
        status: 404,
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/missing" },
    );

    expect(await screen.findByText("Человек не найден")).toBeInTheDocument();
  });

  it("shows a generic error state for a non-404 failure", async () => {
    stubFetch([
      {
        match: "/persons/p1",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/people/:personId" element={<PersonDetailPage />} />
      </Routes>,
      { route: "/people/p1" },
    );

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
  });
});
