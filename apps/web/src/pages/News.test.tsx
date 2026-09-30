import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";

import { HomePage } from "./HomePage";
import { NewsListPage } from "./NewsListPage";
import { NewsDetailPage } from "./NewsDetailPage";
import { NewsManagePage } from "./NewsManagePage";
import { NewsFormPage } from "./NewsFormPage";
import { AdministratorGuard } from "../shell/AdministratorGuard";
import { renderWithProviders, stubFetch } from "../test/renderWithProviders";
import { mockApi, requests } from "../test/mockApi";
import type { News } from "../api/news";

/**
 * TH-0120 / Issue #227 — News / Announcements UI (docs/04-ux/news.md).
 * The backend is the only visibility authority: these tests assert the
 * UI renders exactly what `GET /news` returns and never filters by
 * audience itself.
 */

function meResponse(roleCodes: string[]) {
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
    role_assignments: roleCodes.map((role_code) => ({ role_code, club_id: "club-1", scope_type: "all" })),
  };
}

function news(overrides: Partial<News> = {}): News {
  return {
    id: "n1",
    title: "Осенний поход",
    body: "Собираемся в субботу у школы.",
    status: "published",
    published_at: "2026-09-20T10:00:00Z",
    archived_at: null,
    event_date: null,
    location: null,
    audience_type: "club",
    group_ids: null,
    image_file_id: null,
    linked_event: null,
    created_by: "u0",
    updated_by: null,
    created_at: "2026-09-19T10:00:00Z",
    updated_at: "2026-09-19T10:00:00Z",
    ...overrides,
  };
}

function collection<T>(items: T[]) {
  return { items, pagination: { page: 1, page_size: 20, total: items.length, pages: items.length ? 1 : 0 } };
}

const emptyEvents = collection([]);

/** A News image (as opposed to UI icons/illustrations, also `<img>`). */
const NEWS_IMAGE = 'img[src^="/api/v1/news/"]';

afterEach(() => {
  vi.unstubAllGlobals();
});

function newsListCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/news"));
}

// --- Home -------------------------------------------------------------------

describe("Home — «Новости» block", () => {
  it("is placed before «Ближайшие события» and shows the backend's latest items", async () => {
    const items = [1, 2, 3, 4, 5].map((i) => news({ id: `n${i}`, title: `Новость ${i}` }));
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", response: collection(items) },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    const newsHeading = await screen.findByRole("heading", { name: "Новости" });
    const eventsHeading = screen.getByRole("heading", { name: "Ближайшие события" });
    expect(newsHeading.compareDocumentPosition(eventsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    expect(await screen.findAllByTestId("news-card")).toHaveLength(5);
    // Latest published, at most five — asked from the backend, not computed here.
    expect(newsListCalls(fetchMock)).toEqual(["/api/v1/news?status=published&page=1&page_size=5"]);
    expect(screen.getByRole("link", { name: "Все новости" })).toHaveAttribute("href", "/news");
  });

  it("renders three items when the backend returns three", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["guardian"]) },
      { match: "/me/children", response: collection([]) },
      { match: "/news", response: collection([news({ id: "a" }), news({ id: "b" }), news({ id: "c" })]) },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findAllByTestId("news-card")).toHaveLength(3);
  });

  it("never shows more than five cards", async () => {
    const items = Array.from({ length: 7 }, (_, i) => news({ id: `n${i}` }));
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", response: collection(items) },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findAllByTestId("news-card")).toHaveLength(5);
  });

  it("shows a loading state", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/events", response: emptyEvents },
    ]);
    // `/news` never resolves.
    const pending = vi.fn(() => new Promise<Response>(() => undefined));
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("/news") ? pending() : base(input, init),
    );

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Загружаем новости…")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", response: collection([]) },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Новостей пока нет")).toBeInTheDocument();
  });

  it("shows an error state with retry", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/news",
        status: 500,
        response: { error: { code: "internal_error", message: "Сбой", details: {}, request_id: "r" } },
      },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Не удалось загрузить новости")).toBeInTheDocument();
    const before = newsListCalls(fetchMock).length;
    await userEvent.setup().click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(newsListCalls(fetchMock).length).toBeGreaterThan(before));
  });

  it("renders a card with an image and a card without one", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/news",
        response: collection([
          news({ id: "with", title: "С картинкой", image_file_id: "f1" }),
          news({ id: "without", title: "Без картинки" }),
        ]),
      },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    const [withImage, withoutImage] = await screen.findAllByTestId("news-card");
    expect(within(withImage).getByText("С картинкой")).toBeInTheDocument();
    expect(withImage.querySelector(NEWS_IMAGE)).toHaveAttribute("src", "/api/v1/news/with/image?v=f1");
    expect(withoutImage.querySelector(NEWS_IMAGE)).toBeNull();
    expect(within(withoutImage).getByText("Без картинки")).toBeInTheDocument();
    expect(withImage).toHaveAttribute("href", "/news/with");
  });

  it("offers «Управление новостями» to the Administrator only", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/news", response: collection([]) },
      { match: "/events", response: emptyEvents },
    ]);
    renderWithProviders(<HomePage />);
    expect(await screen.findByRole("link", { name: "Управление новостями" })).toHaveAttribute(
      "href",
      "/news/manage",
    );
  });

  it.each(["member", "guardian", "instructor"])("hides management from %s", async (role) => {
    stubFetch([
      { match: "/auth/me", response: meResponse([role]) },
      { match: "/me/children", response: collection([]) },
      { match: "/news", response: collection([news()]) },
      { match: "/events", response: emptyEvents },
    ]);
    renderWithProviders(<HomePage />);
    await screen.findAllByTestId("news-card");
    expect(screen.queryByText("Управление новостями")).not.toBeInTheDocument();
  });

  it("does not compute audience: every item the backend returns is shown as-is", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/news",
        response: collection([
          news({ id: "g", title: "Для групп", audience_type: "groups", group_ids: null }),
          news({ id: "c", title: "Для клуба", audience_type: "club" }),
        ]),
      },
      { match: "/events", response: emptyEvents },
    ]);

    renderWithProviders(<HomePage />);

    expect(await screen.findByText("Для групп")).toBeInTheDocument();
    expect(screen.getByText("Для клуба")).toBeInTheDocument();
    // No membership/group/role lookups are made to decide News visibility.
    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls.some((url) => url.includes("/groups") || url.includes("/memberships"))).toBe(false);
    expect(newsListCalls(fetchMock).every((url) => !/group|role|audience/.test(url))).toBe(true);
  });
});

// --- All news ---------------------------------------------------------------

describe("«Все новости»", () => {
  it("lists published News with the admin contextual action", async () => {
    const fetchMock = stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/news", response: collection([news(), news({ id: "n2", title: "Соревнования" })]) },
    ]);

    renderWithProviders(<NewsListPage />, { route: "/news" });

    expect(await screen.findAllByTestId("news-card")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Управление новостями" })).toBeInTheDocument();
    expect(newsListCalls(fetchMock)[0]).toContain("status=published");
  });

  it("has no management action for a non-admin", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", response: collection([news()]) },
    ]);
    renderWithProviders(<NewsListPage />, { route: "/news" });
    await screen.findAllByTestId("news-card");
    expect(screen.queryByText("Управление новостями")).not.toBeInTheDocument();
  });

  it("shows empty and error states", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", response: collection([]) },
    ]);
    const first = renderWithProviders(<NewsListPage />, { route: "/news" });
    expect(await screen.findByText("Новостей пока нет")).toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();

    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news", status: 500, response: { error: { code: "x", message: "Сбой", details: {}, request_id: "r" } } },
    ]);
    renderWithProviders(<NewsListPage />, { route: "/news" });
    expect(await screen.findByText("Не удалось загрузить новости")).toBeInTheDocument();
  });
});

// --- Detail -----------------------------------------------------------------

function renderDetail(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/news/:newsId" element={<NewsDetailPage />} />
    </Routes>,
    { route },
  );
}

describe("News detail", () => {
  it("shows all present fields and navigation to the linked Event", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["guardian"]) },
      {
        match: "/news/n1",
        response: news({
          image_file_id: "img",
          event_date: "2026-10-10",
          location: "Лесопарк",
          linked_event: {
            id: "ev-1",
            title: "Поход выходного дня",
            start_at: "2026-10-10T09:00:00+03:00",
            end_at: "2026-10-10T18:00:00+03:00",
            status: "published",
          },
        }),
      },
    ]);

    renderDetail("/news/n1");

    expect(await screen.findByRole("heading", { name: "Осенний поход" })).toBeInTheDocument();
    expect(screen.getByText("Собираемся в субботу у школы.")).toBeInTheDocument();
    expect(screen.getByText("20 сентября 2026 г.")).toBeInTheDocument();
    expect(screen.getByText("10 октября 2026 г.")).toBeInTheDocument();
    expect(screen.getByText("Лесопарк")).toBeInTheDocument();
    expect(document.querySelector(NEWS_IMAGE)).toHaveAttribute("src", "/api/v1/news/n1/image?v=img");
    expect(screen.getByText("Поход выходного дня")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Перейти к событию/ })).toHaveAttribute(
      "href",
      "/events?date=2026-10-10&event=ev-1",
    );
    // Ordinary readers see no status badge / edit action.
    expect(screen.queryByText("Опубликована")).not.toBeInTheDocument();
    expect(screen.queryByText("Редактировать")).not.toBeInTheDocument();
  });

  it("renders without optional fields", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      { match: "/news/n1", response: news() },
    ]);
    renderDetail("/news/n1");
    await screen.findByRole("heading", { name: "Осенний поход" });
    expect(document.querySelector(NEWS_IMAGE)).toBeNull();
    expect(screen.queryByText("Связанное событие")).not.toBeInTheDocument();
    expect(screen.queryByText("Место")).not.toBeInTheDocument();
  });

  it("treats a backend 404 (draft/archived/other audience) as not found", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["member"]) },
      {
        match: "/news/hidden",
        status: 404,
        response: { error: { code: "not_found", message: "News not found", details: {}, request_id: "r" } },
      },
    ]);
    renderDetail("/news/hidden");
    expect(await screen.findByText("Новость не найдена")).toBeInTheDocument();
  });

  it("shows status and edit action to the Administrator", async () => {
    stubFetch([
      { match: "/auth/me", response: meResponse(["admin"]) },
      { match: "/news/n1", response: news({ status: "draft", published_at: null }) },
    ]);
    renderDetail("/news/n1");
    expect(await screen.findByText("Черновик")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Редактировать/ })).toHaveAttribute(
      "href",
      "/news/manage/n1/edit",
    );
  });
});

// --- Management ---------------------------------------------------------------

function renderManagement(route: string, roles: string[], handlers: Parameters<typeof mockApi>[0]) {
  const fetchMock = mockApi([{ match: "/auth/me", body: meResponse(roles) }, ...handlers]);
  renderWithProviders(
    <Routes>
      <Route element={<AdministratorGuard />}>
        <Route path="/news/manage" element={<NewsManagePage />} />
        <Route path="/news/manage/new" element={<NewsFormPage />} />
        <Route path="/news/manage/:newsId/edit" element={<NewsFormPage />} />
      </Route>
    </Routes>,
    { route },
  );
  return fetchMock;
}

const groups = collection([
  {
    id: "g1",
    club_id: "club-1",
    name: "Юные туристы",
    description: null,
    status: "active",
    valid_from: "2020-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2020-01-01T00:00:00Z",
    updated_at: "2020-01-01T00:00:00Z",
  },
  {
    id: "g2",
    club_id: "club-1",
    name: "Скалолазы",
    description: null,
    status: "active",
    valid_from: "2020-01-01T00:00:00Z",
    valid_to: null,
    created_at: "2020-01-01T00:00:00Z",
    updated_at: "2020-01-01T00:00:00Z",
  },
]);

describe("News management (Administrator)", () => {
  it("is forbidden for a non-admin by direct URL", async () => {
    renderManagement("/news/manage", ["member"], []);
    expect(await screen.findByText("Раздел недоступен")).toBeInTheDocument();
  });

  it("lists all statuses and publishes a draft", async () => {
    const fetchMock = renderManagement("/news/manage", ["admin"], [
      { method: "POST", match: "/news/d1/publish", body: news({ id: "d1", title: "Черновик 1" }) },
      {
        method: "GET",
        match: "/news?status=all",
        body: collection([
          news({ id: "d1", title: "Черновик 1", status: "draft", published_at: null }),
          news({ id: "p1", title: "Опубликованная" }),
          news({ id: "a1", title: "Архивная", status: "archived", archived_at: "2026-09-25T00:00:00Z" }),
        ]),
      },
    ]);

    const draftRow = (await screen.findByText("Черновик 1")).closest("li") as HTMLElement;
    const archivedRow = screen.getByText("Архивная").closest("li") as HTMLElement;
    expect(within(archivedRow).queryByRole("button", { name: /Архивировать/ })).not.toBeInTheDocument();
    expect(within(archivedRow).queryByText("Изменить")).not.toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(within(draftRow).getByRole("button", { name: "Опубликовать" }));
    const dialog = await screen.findByRole("dialog", { name: "Опубликовать новость?" });
    await user.click(within(dialog).getByRole("button", { name: "Опубликовать" }));

    await waitFor(() =>
      expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/news/d1/publish"]),
    );
  });

  it("archives a published News after confirmation", async () => {
    const fetchMock = renderManagement("/news/manage", ["admin"], [
      { method: "POST", match: "/news/p1/archive", body: news({ id: "p1", status: "archived" }) },
      { method: "GET", match: "/news?status=all", body: collection([news({ id: "p1", title: "Опубликованная" })]) },
    ]);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Архивировать/ }));
    const dialog = await screen.findByRole("dialog", { name: "Архивировать новость?" });
    await user.click(within(dialog).getByRole("button", { name: "Архивировать" }));

    await waitFor(() =>
      expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/news/p1/archive"]),
    );
    expect(requests(fetchMock).some(([method]) => method === "DELETE")).toBe(false);
  });

  it("creates a group-targeted News with an Event link, publishing it immediately", async () => {
    let created: Record<string, unknown> | null = null;
    const fetchMock = renderManagement("/news/manage/new", ["admin"], [
      { method: "GET", match: "/groups", body: groups },
      {
        method: "GET",
        match: "/events?",
        body: collection([
          {
            id: "ev-1",
            club_id: "club-1",
            event_type: "trip",
            title: "Поход",
            description: null,
            start_at: "2026-10-10T09:00:00Z",
            end_at: "2026-10-10T18:00:00Z",
            timezone: "Europe/Moscow",
            status: "published",
          },
        ]),
      },
      {
        method: "POST",
        match: "/api/v1/news",
        body: (init: RequestInit | undefined) => {
          created = JSON.parse(String(init?.body));
          return news({ id: "new" });
        },
      },
      { method: "GET", match: "/news?status=all", body: collection([]) },
    ]);

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Заголовок"), "Новый поход");
    await user.type(screen.getByLabelText("Текст"), "Подробности");
    await user.type(screen.getByLabelText("Место"), "Парк");
    await user.click(screen.getByLabelText("Выбранные группы"));

    const submit = screen.getByRole("button", { name: "Создать" });
    // Selected groups are required for the `groups` audience.
    expect(await screen.findByText("Выберите хотя бы одну группу.")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    await user.click(await screen.findByLabelText("Скалолазы"));
    await waitFor(() => expect(screen.getByRole("option", { name: /Поход/ })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Связанное событие (необязательно)"), "ev-1");
    await user.click(screen.getByLabelText("Опубликовать"));
    await user.click(submit);

    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toEqual({
      title: "Новый поход",
      body: "Подробности",
      audience_type: "groups",
      group_ids: ["g2"],
      event_date: null,
      location: "Парк",
      event_id: "ev-1",
      status: "published",
    });
    expect(requests(fetchMock).filter(([method]) => method === "POST")).toHaveLength(1);
  });

  it("continues on the edit page when the image upload fails after creation (no duplicate)", async () => {
    Object.assign(URL, { createObjectURL: () => "blob:preview", revokeObjectURL: () => undefined });
    const fetchMock = renderManagement("/news/manage/new", ["admin"], [
      { method: "GET", match: "/groups", body: groups },
      { method: "GET", match: "/events?", body: collection([]) },
      {
        method: "PUT",
        match: "/news/new1/image",
        status: 422,
        body: { error: { code: "invalid_image", message: "Плохое изображение", details: {}, request_id: "r" } },
      },
      { method: "POST", match: "/api/v1/news", body: news({ id: "new1", status: "draft" }) },
      { method: "GET", match: "/news/new1", body: news({ id: "new1", status: "draft", group_ids: [] }) },
    ]);

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Заголовок"), "Заголовок");
    await user.type(screen.getByLabelText("Текст"), "Текст");
    await user.upload(
      screen.getByLabelText("Изображение (необязательно)"),
      new File(["x"], "photo.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByRole("heading", { name: "Редактирование новости" })).toBeInTheDocument();
    expect(requests(fetchMock).filter(([method]) => method === "POST")).toHaveLength(1);
  });

  it("edits a draft (PATCH), replaces its image and publishes it", async () => {
    // jsdom has no object URLs; the form previews the picked file with one.
    Object.assign(URL, { createObjectURL: () => "blob:preview", revokeObjectURL: () => undefined });
    let patched: Record<string, unknown> | null = null;
    const fetchMock = renderManagement("/news/manage/d1/edit", ["admin"], [
      { method: "GET", match: "/groups", body: groups },
      { method: "GET", match: "/events?", body: collection([]) },
      { method: "PUT", match: "/news/d1/image", body: news({ id: "d1", status: "draft", image_file_id: "f2" }) },
      { method: "POST", match: "/news/d1/publish", body: news({ id: "d1" }) },
      {
        method: "PATCH",
        match: "/news/d1",
        body: (init: RequestInit | undefined) => {
          patched = JSON.parse(String(init?.body));
          return news({ id: "d1", status: "draft", published_at: null });
        },
      },
      {
        method: "GET",
        match: "/news/d1",
        body: news({
          id: "d1",
          title: "Черновик",
          status: "draft",
          published_at: null,
          audience_type: "groups",
          group_ids: ["g1"],
          image_file_id: "f1",
        }),
      },
      { method: "GET", match: "/news?status=all", body: collection([]) },
    ]);

    const user = userEvent.setup();
    const title = await screen.findByLabelText("Заголовок");
    expect(title).toHaveValue("Черновик");
    expect(await screen.findByLabelText("Юные туристы")).toBeChecked();
    expect(screen.getByAltText("Предпросмотр изображения")).toHaveAttribute(
      "src",
      "/api/v1/news/d1/image?v=f1",
    );

    await user.clear(title);
    await user.type(title, "Обновлено");
    await user.upload(
      screen.getByLabelText("Изображение (необязательно)"),
      new File(["x"], "photo.png", { type: "image/png" }),
    );
    await user.click(screen.getByLabelText("Весь клуб"));
    await user.click(screen.getByLabelText("Опубликовать"));
    await user.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(requests(fetchMock)).toContainEqual(["POST", "/api/v1/news/d1/publish"]),
    );
    expect(patched).toMatchObject({ title: "Обновлено", audience_type: "club", group_ids: [] });
    const mutationOrder = requests(fetchMock)
      .filter(([method]) => method !== "GET")
      .map(([method, url]) => `${method} ${url}`);
    expect(mutationOrder).toEqual([
      "PATCH /api/v1/news/d1",
      "PUT /api/v1/news/d1/image",
      "POST /api/v1/news/d1/publish",
    ]);
  });
});

// --- Responsive ---------------------------------------------------------------

describe("News responsive layout", () => {
  it("uses the shared responsive grid for cards (single column below 768px)", async () => {
    const css = readFileSync(join(process.cwd(), "src/pages/News.module.css"), "utf8");
    expect(css).toMatch(/grid-template-columns: repeat\(auto-fill, minmax\(260px, 1fr\)\)/);
    expect(css).toMatch(/@media \(max-width: 767\.98px\)[\s\S]*\.grid[\s\S]*minmax\(0, 1fr\)/);
  });
});
