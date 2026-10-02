import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "./AppShell";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";

const NAVIGATION_LABELS = [
  "Главная",
  "Люди",
  "Группы",
  "События",
  "Достижения",
  "Отчёты",
  "Склад",
  "Настройки",
];

function meResponse(roleCodes: string[] = ["admin"]) {
  return {
    user: {
      id: "u1",
      login_identifier: "user@example.com",
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

function LoginProbe() {
  const location = useLocation();
  return <div>Login page {location.search}</div>;
}

function renderShell(route = "/groups") {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginProbe />} />
      <Route element={<AppShell />}>
        <Route path="*" element={<div>Protected page content</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppShell authentication boundary (TH-0089 / UI-FOUNDATION-SPEC §3.1)", () => {
  it("renders the shell, profile and protected content for an authenticated user", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderShell();

    expect(await screen.findByText("Protected page content")).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByAltText("TourCRM «Вектор»")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Иванова Анна/ })).toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("shows an explicit resolution state while /auth/me is pending — no shell, no placeholder identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    renderShell();

    expect(screen.getByText("Проверка входа…").closest("[role=status]")).toBeInTheDocument();
    expect(screen.queryByText("Protected page content")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("routes a 401 from /auth/me to /login with the current route as the return target", async () => {
    stubFetch([{ match: "/auth/me", response: {}, status: 401 }]);
    renderShell("/groups?status=active");

    expect(await screen.findByText(/Login page/)).toHaveTextContent(
      `?next=${encodeURIComponent("/groups?status=active")}`,
    );
    expect(screen.queryByText("Protected page content")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("does not treat a non-401 failure as a logout: stays put with a retryable error state", async () => {
    const fetchMock = stubFetch([
      {
        match: "/auth/me",
        response: { error: { code: "internal_error", message: "Сбой", details: {}, request_id: "r1" } },
        status: 500,
      },
    ]);
    renderShell();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось проверить вход");
    expect(screen.queryByText(/Login page/)).not.toBeInTheDocument();
    expect(screen.queryByText("Protected page content")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();

    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify(meResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Protected page content")).toBeInTheDocument();
  });

  it("does not treat a network failure as a logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderShell();

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось проверить вход");
    expect(screen.queryByText(/Login page/)).not.toBeInTheDocument();
  });
});

describe("AppShell navigation and mobile drawer (TH-0089 regression)", () => {
  it("keeps exactly the eight approved navigation items in order", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderShell("/");

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(
      NAVIGATION_LABELS,
    );
  });

  it("opens the drawer from the menu control, shows the brand and all eight items, and closes via the close control", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderShell("/");
    const user = userEvent.setup();

    const menuButton = await screen.findByRole("button", { name: "Меню" });
    await user.click(menuButton);

    const drawer = screen.getByRole("dialog", { name: "Навигация" });
    expect(within(drawer).getByAltText("TourCRM «Вектор»")).toBeInTheDocument();
    expect(within(drawer).getAllByRole("link").map((link) => link.textContent)).toEqual(
      NAVIGATION_LABELS,
    );

    const closeButton = within(drawer).getByRole("button", { name: "Закрыть меню" });
    expect(closeButton).toHaveFocus();
    await user.click(closeButton);

    expect(screen.queryByRole("dialog", { name: "Навигация" })).not.toBeInTheDocument();
    expect(menuButton).toHaveFocus();
  });

  it("closes the drawer on Escape and after choosing a navigation item", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse() }]);
    renderShell("/");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Меню" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Навигация" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Меню" }));
    const drawer = screen.getByRole("dialog", { name: "Навигация" });
    await user.click(within(drawer).getByRole("link", { name: "Группы" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Навигация" })).not.toBeInTheDocument(),
    );
  });
});

describe("AppShell role-aware navigation (TH-0120 / Issue #181, UNION)", () => {
  const cases: Array<{ name: string; roles: string[]; expected: string[] }> = [
    { name: "Administrator", roles: ["admin"], expected: NAVIGATION_LABELS },
    {
      name: "Instructor",
      roles: ["instructor"],
      expected: ["Главная", "Люди", "Группы", "События", "Достижения", "Настройки"],
    },
    {
      name: "Member",
      roles: ["member"],
      expected: ["Главная", "Группы", "События", "Достижения", "Настройки"],
    },
    {
      name: "Guardian",
      roles: ["guardian"],
      expected: ["Главная", "События", "Достижения", "Настройки"],
    },
    {
      name: "Guardian + Instructor (UNION)",
      roles: ["guardian", "instructor"],
      expected: ["Главная", "Люди", "Группы", "События", "Достижения", "Настройки"],
    },
  ];

  it.each(cases)("$name sees exactly the allowed sections in the sidebar, in canonical order", async ({ roles, expected }) => {
    stubFetch([{ match: "/auth/me", response: meResponse(roles) }]);
    renderShell("/");

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(expected);
    expect(screen.queryByText(/Гость/)).not.toBeInTheDocument();
  });

  it("applies the same role-aware list in the mobile drawer", async () => {
    stubFetch([{ match: "/auth/me", response: meResponse(["guardian"]) }]);
    renderShell("/");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Меню" }));
    const drawer = screen.getByRole("dialog", { name: "Навигация" });
    expect(within(drawer).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Главная",
      "События",
      "Достижения",
      "Настройки",
    ]);
  });

  it("the shell itself does not gate routes: hidden items are removed from navigation only", async () => {
    // Section gating on direct visits lives in SectionGuard on the app
    // routes (Issue #212, covered in App.test.tsx); backend authorization
    // stays authoritative for whatever a page then requests.
    stubFetch([{ match: "/auth/me", response: meResponse(["guardian"]) }]);
    renderShell("/reports");

    expect(await screen.findByText("Protected page content")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(within(nav).queryByRole("link", { name: "Отчёты" })).not.toBeInTheDocument();
  });
});

describe("AppShell role-aware navigation — Reports visibility (Issue #212)", () => {
  it.each([
    { roles: ["admin"], visible: true },
    { roles: ["instructor"], visible: false },
    { roles: ["member"], visible: false },
    { roles: ["guardian"], visible: false },
    { roles: ["instructor", "member", "guardian"], visible: false },
    { roles: ["member", "admin"], visible: true },
  ])("roles $roles → Отчёты visible: $visible", async ({ roles, visible }) => {
    stubFetch([{ match: "/auth/me", response: meResponse(roles) }]);
    renderShell("/");

    const nav = await screen.findByRole("navigation", { name: "Основная навигация" });
    const reports = within(nav).queryByRole("link", { name: "Отчёты" });
    if (visible) expect(reports).toBeInTheDocument();
    else expect(reports).not.toBeInTheDocument();
    // Achievements stays visible for every role (feature not implemented yet).
    expect(within(nav).getByRole("link", { name: "Достижения" })).toBeInTheDocument();
    // No active-role switcher is introduced.
    expect(screen.queryByRole("combobox", { name: /роль/i })).not.toBeInTheDocument();
  });
});
