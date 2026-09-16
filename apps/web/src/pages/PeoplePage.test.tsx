import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PeoplePage } from "./PeoplePage";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

function peopleResponse(
  items: Array<{ id: string; first_name: string; last_name: string; birth_date: string | null }>,
  pagination: { page: number; page_size: number; total: number; pages: number },
) {
  return {
    items: items.map((item) => ({
      id: item.id,
      first_name: item.first_name,
      last_name: item.last_name,
      middle_name: null,
      birth_date: item.birth_date,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    })),
    pagination,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PeoplePage", () => {
  it("shows a loading state before the list resolves", () => {
    stubFetch([
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(screen.getByText("Загружаем людей…")).toBeInTheDocument();
  });

  it("renders a readable list of people on success", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [
            { id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: "2012-05-01" },
            { id: "p2", first_name: "Пётр", last_name: "Сидоров", birth_date: null },
          ],
          { page: 1, page_size: 20, total: 2, pages: 1 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByRole("link", { name: "Иванова Анна" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Сидоров Пётр" })).toBeInTheDocument();
    expect(screen.getByText(/Дата рождения/)).toBeInTheDocument();
  });

  it("shows the empty-people state when there are no people at all", async () => {
    stubFetch([
      { match: "/persons?page=1", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByText("Пока нет ни одного человека")).toBeInTheDocument();
  });

  it("shows the no-results state when a search matches nobody", async () => {
    const fetchMock = stubFetch([
      { match: "/persons?page=1&page_size=20&search=", response: peopleResponse([], { page: 1, page_size: 20, total: 0, pages: 0 }) },
      { match: "/persons?page=1", response: peopleResponse([{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null }], { page: 1, page_size: 20, total: 1, pages: 1 }) },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByRole("link", { name: "Иванова Анна" });

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Поиск по имени"), "Зз");

    await waitFor(
      () => {
        expect(
          fetchMock.mock.calls.some(([input]) => String(input).includes("search=%D0%97%D0%B7")),
        ).toBe(true);
      },
      { timeout: 2000 },
    );
    expect(await screen.findByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("shows an error state when the request fails", async () => {
    stubFetch([
      {
        match: "/persons?page=1",
        response: { error: { code: "internal_error", message: "Сбой сервера", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);

    renderWithProviders(<PeoplePage />);

    expect(await screen.findByText("Не удалось загрузить людей")).toBeInTheDocument();
  });

  it("moves to the next page and requests it from the server", async () => {
    const fetchMock = stubFetch([
      {
        match: "/persons?page=2",
        response: peopleResponse(
          [{ id: "p3", first_name: "Олег", last_name: "Петров", birth_date: null }],
          { page: 2, page_size: 20, total: 21, pages: 2 },
        ),
      },
      {
        match: "/persons?page=1",
        response: peopleResponse(
          [{ id: "p1", first_name: "Анна", last_name: "Иванова", birth_date: null }],
          { page: 1, page_size: 20, total: 21, pages: 2 },
        ),
      },
    ]);

    renderWithProviders(<PeoplePage />);
    await screen.findByRole("link", { name: "Иванова Анна" });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByRole("link", { name: "Петров Олег" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/persons?page=2"))).toBe(true);
  });
});
