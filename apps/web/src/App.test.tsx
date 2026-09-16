import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { App } from "./App";
import { stubFetch } from "./test/renderWithProviders";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("renders exactly the seven approved navigation items, the brand logo and a profile area, and lands on Home", async () => {
    stubFetch([
      { match: "/auth/me", response: {}, status: 401 },
      {
        match: "/events",
        response: { items: [], pagination: { page: 1, page_size: 5, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    const expectedItems = [
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Настройки",
    ];
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(7);
    expectedItems.forEach((label) => {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    });

    expect(screen.getByAltText("TourCRM «Вектор»")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Гость/ })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /TourCRM/ })).toBeInTheDocument();
  });

  it("gives the current page an accessible current-page marker", async () => {
    stubFetch([
      { match: "/auth/me", response: {}, status: 401 },
      {
        match: "/events",
        response: { items: [], pagination: { page: 1, page_size: 5, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    const homeLink = await screen.findByRole("link", { name: "Главная" });
    expect(homeLink).toHaveAttribute("aria-current", "page");
  });

  it("renders the real People Core screen at /people, not PlaceholderPage (TH-0094 regression)", async () => {
    window.history.pushState({}, "", "/people");
    stubFetch([
      { match: "/auth/me", response: {}, status: 401 },
      {
        match: "/persons",
        response: { items: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } },
      },
    ]);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Люди" })).toBeInTheDocument();
    expect(screen.queryByText(/появится в одном из следующих этапов/)).not.toBeInTheDocument();
  });
});
