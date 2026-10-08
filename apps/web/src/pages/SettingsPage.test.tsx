import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SettingsPage } from "./SettingsPage";
import { CROP_VIEWPORT_SIZE } from "./PhotoCropDialog";
import { ProfileMenu } from "../shell/ProfileMenu";
import { createTestQueryClient, renderWithProviders } from "../test/renderWithProviders";

type FakeResponse = { status: number; body?: unknown };

type FakeBackendOptions = {
  /** `GET /persons/p1` status other than 200 (e.g. 404 for a role without
   * `person.read(self)`). */
  personStatus?: number;
  /** Replaces the default PATCH handling (which applies the body). Return
   * a Promise to hold the request open. */
  patch?: (body: Record<string, unknown>) => FakeResponse | Promise<FakeResponse>;
  /** Replaces the default (204) `/auth/password/change` response. */
  passwordChange?: (body: Record<string, unknown>) => FakeResponse | Promise<FakeResponse>;
};

/** Stateful fake backend: `/auth/me` reflects PUT/DELETE photo and PATCH
 * name changes, so the tests observe the real cache update + refetch
 * path. */
function fakeBackend(initialPhotoFileId: string | null, options: FakeBackendOptions = {}) {
  let photoFileId = initialPhotoFileId;
  let version = 1;
  const person = {
    id: "p1",
    first_name: "Анна",
    last_name: "Иванова",
    middle_name: null as string | null,
    birth_date: "2001-03-04",
    phone: "+79990000000" as string | null,
    email: "anna@example.com",
    address: "Москва" as string | null,
  };
  const personOut = () => ({
    ...person,
    photo_file_id: photoFileId,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: `2026-01-0${version}T00:00:00Z`,
    role_codes: ["member"],
  });
  const me = () => ({
    user: {
      id: "u1",
      login_identifier: "user@example.com",
      status: "active",
      email_verified_at: null,
      person: {
        id: "p1",
        first_name: person.first_name,
        last_name: person.last_name,
        middle_name: person.middle_name,
        birth_date: person.birth_date,
        photo_file_id: photoFileId,
      },
    },
    role_assignments: [],
  });
  const json = ({ status, body }: FakeResponse) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.endsWith("/auth/me")) {
      return new Response(JSON.stringify(me()), { status: 200 });
    }
    if (url.endsWith("/persons/p1") && method === "GET") {
      if (options.personStatus) {
        return json({
          status: options.personStatus,
          body: { error: { code: "person_not_found", message: "Person not found" } },
        });
      }
      return new Response(JSON.stringify(personOut()), { status: 200 });
    }
    if (url.endsWith("/persons/p1") && method === "PATCH") {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      if (options.patch) return json(await options.patch(body));
      Object.assign(person, body);
      version += 1;
      return new Response(JSON.stringify(personOut()), { status: 200 });
    }
    if (url.endsWith("/auth/password/change") && method === "POST") {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      if (options.passwordChange) return json(await options.passwordChange(body));
      return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    }
    if (url.endsWith("/persons/p1/photo") && method === "PUT") {
      photoFileId = "file-new";
      return new Response(JSON.stringify({ id: "p1", photo_file_id: photoFileId }), { status: 200 });
    }
    if (url.endsWith("/persons/p1/photo") && method === "DELETE") {
      photoFileId = null;
      return new Response(null, { status: 204 });
    }
    throw new Error(`Unexpected fetch ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: ReturnType<typeof fakeBackend>, suffix: string, method: string) {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input).endsWith(suffix) && (init?.method ?? "GET").toUpperCase() === method,
  );
}

const drawImage = vi.fn();

// jsdom has no PointerEvent; without it `fireEvent.pointer*` drops
// clientX/pointerId. A MouseEvent subclass carrying `pointerId` is enough.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 0;
    }
  }
  window.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
}

beforeEach(() => {
  drawImage.mockReset();
  vi.stubGlobal(
    "URL",
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:local"), revokeObjectURL: vi.fn() }),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => ({ drawImage, fillRect: vi.fn(), fillStyle: "" }) as unknown as CanvasRenderingContext2D,
  );
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (callback) {
    callback(new Blob(["webp"], { type: "image/webp" }));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSettingsWithMenu() {
  return renderWithProviders(
    <>
      <ProfileMenu />
      <SettingsPage />
    </>,
    { route: "/settings" },
  );
}

function menuAvatarImg(): HTMLImageElement | null {
  return screen.getByRole("button", { name: /Иванова Анна/ }).querySelector("img");
}

/** Selects a file and simulates the browser finishing decoding it as a
 * 800×400 image (jsdom does not decode images). */
async function openCropper() {
  const input = screen.getByTestId("profile-photo-input");
  const file = new File(["img"], "me.jpg", { type: "image/jpeg" });
  await userEvent.setup().upload(input, file);
  const dialog = await screen.findByRole("dialog", { name: "Фотография профиля" });
  const img = within(dialog).getByAltText("Выбранная фотография") as HTMLImageElement;
  Object.defineProperty(img, "naturalWidth", { value: 800 });
  Object.defineProperty(img, "naturalHeight", { value: 400 });
  fireEvent.load(img);
  return { dialog, img };
}

function translateOf(img: HTMLImageElement): [number, number] {
  const match = /translate\((-?[\d.]+)px, (-?[\d.]+)px\)/.exec(img.style.transform);
  if (!match) throw new Error(`transform: ${img.style.transform}`);
  return [Number(match[1]), Number(match[2])];
}

describe("SettingsPage profile photo (TH-0119)", () => {
  it("shows the initials fallback and no delete action when there is no photo", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();

    const change = await screen.findByRole("button", { name: /Изменить фотографию/ });
    expect(change).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить фотографию/ })).not.toBeInTheDocument();
    expect(menuAvatarImg()).toBeNull();
    expect(screen.getByRole("button", { name: /Иванова Анна/ })).toHaveTextContent("ИА");
  });

  it("renders the current photo with a photo_file_id-versioned URL", async () => {
    fakeBackend("file-1");
    renderSettingsWithMenu();

    await screen.findByRole("button", { name: /Удалить фотографию/ });
    expect(menuAvatarImg()).toHaveAttribute("src", "/api/v1/persons/p1/photo?v=file-1");
  });

  it("opens a round crop editor with a dimmed outside area", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });

    const { dialog, img } = await openCropper();

    const viewport = within(dialog).getByTestId("photo-crop-viewport");
    expect(viewport.className).toMatch(/viewport/);
    expect(viewport).toHaveStyle({ width: `${CROP_VIEWPORT_SIZE}px`, height: `${CROP_VIEWPORT_SIZE}px` });
    // Cover-fit: the 800×400 photo's short side fills the circle.
    expect(img).toHaveStyle({ width: "480px", height: `${CROP_VIEWPORT_SIZE}px` });
    expect(translateOf(img)).toEqual([0, 0]);
  });

  it("pans by dragging and with arrow keys, clamped so the circle stays covered", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });
    const { dialog, img } = await openCropper();
    const stage = within(dialog).getByTestId("photo-crop-stage");

    fireEvent.pointerDown(stage, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(stage, { pointerId: 1, clientX: 150, clientY: 130 });
    fireEvent.pointerUp(stage, { pointerId: 1 });
    // Horizontal pan allowed (±120px); vertical clamped at 0 (no slack).
    expect(translateOf(img)).toEqual([50, 0]);

    fireEvent.keyDown(stage, { key: "ArrowLeft" });
    expect(translateOf(img)).toEqual([40, 0]);

    fireEvent.pointerDown(stage, { pointerId: 2, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(stage, { pointerId: 2, clientX: 500, clientY: 0 });
    expect(translateOf(img)).toEqual([120, 0]);
  });

  it("zooms in and out", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });
    const { dialog, img } = await openCropper();
    const zoomOut = within(dialog).getByRole("button", { name: "Уменьшить" });
    const zoomIn = within(dialog).getByRole("button", { name: "Увеличить" });

    expect(zoomOut).toBeDisabled();
    await userEvent.setup().click(zoomIn);
    expect(img).toHaveStyle({ width: "600px", height: "300px" });
    expect(within(dialog).getByRole("slider", { name: "Масштаб" })).toHaveValue("1.25");

    await userEvent.setup().click(zoomOut);
    expect(img).toHaveStyle({ width: "480px", height: "240px" });
  });

  it("cancel closes the editor without uploading", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });
    const { dialog } = await openCropper();

    await userEvent.setup().click(within(dialog).getByRole("button", { name: /Отмена/ }));

    expect(screen.queryByRole("dialog", { name: "Фотография профиля" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("save uploads the user's crop and the ProfileMenu avatar updates without reload", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });
    const { dialog } = await openCropper();
    const stage = within(dialog).getByTestId("photo-crop-stage");
    fireEvent.keyDown(stage, { key: "ArrowRight" });

    await userEvent.setup().click(within(dialog).getByRole("button", { name: /Сохранить/ }));

    // The exported square is the circle's bounding square at the user's
    // pan/zoom: 240/0.6 = 400 source px, shifted 10/0.6 px left of centre.
    const [, sx, sy, sw, sh, dx, dy, dw, dh] = drawImage.mock.calls[0]!;
    expect(sw).toBeCloseTo(400);
    expect(sh).toBeCloseTo(400);
    expect(sx).toBeCloseTo(200 - 10 / 0.6);
    expect(sy).toBeCloseTo(0);
    expect([dx, dy, dw, dh]).toEqual([0, 0, 512, 512]);

    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(String(put[0])).toBe("/api/v1/persons/p1/photo");
    expect((put[1]!.body as FormData).get("photo")).toBeInstanceOf(Blob);

    await waitFor(() =>
      expect(menuAvatarImg()).toHaveAttribute("src", "/api/v1/persons/p1/photo?v=file-new"),
    );
    expect(screen.queryByRole("dialog", { name: "Фотография профиля" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Удалить фотографию/ })).toBeInTheDocument();
  });

  it("delete returns the ProfileMenu to initials without reload", async () => {
    const fetchMock = fakeBackend("file-1");
    renderSettingsWithMenu();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Удалить фотографию/ }));
    const confirm = await screen.findByRole("dialog", { name: "Удалить фотографию?" });
    await user.click(within(confirm).getByRole("button", { name: "Удалить фотографию" }));

    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
    await waitFor(() => expect(menuAvatarImg()).toBeNull());
    expect(screen.getByRole("button", { name: /Иванова Анна/ })).toHaveTextContent("ИА");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Удалить фотографию/ })).not.toBeInTheDocument(),
    );
  });

  it("reports an undecodable image instead of opening the editor", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();
    await screen.findByRole("button", { name: /Изменить фотографию/ });
    await userEvent
      .setup()
      .upload(screen.getByTestId("profile-photo-input"), new File(["x"], "x.png", { type: "image/png" }));
    const dialog = await screen.findByRole("dialog", { name: "Фотография профиля" });

    act(() => {
      fireEvent.error(within(dialog).getByAltText("Выбранная фотография"));
    });

    expect(screen.queryByRole("dialog", { name: "Фотография профиля" })).not.toBeInTheDocument();
    expect(await screen.findByText(/Не удалось открыть изображение/)).toBeInTheDocument();
  });
});

// --- TH #311: own Person data -------------------------------------------------

async function profileForm() {
  return screen.findByRole("form", { name: "Данные профиля" });
}

describe("SettingsPage profile data (TH #311)", () => {
  it("loads the user's own Person and fills the editable fields", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const form = await profileForm();

    expect(within(form).getByLabelText("Фамилия")).toHaveValue("Иванова");
    expect(within(form).getByLabelText("Имя")).toHaveValue("Анна");
    expect(within(form).getByLabelText("Отчество")).toHaveValue("");
    expect(within(form).getByLabelText("Телефон")).toHaveValue("+79990000000");
    expect(within(form).getByLabelText("Адрес")).toHaveValue("Москва");
    expect(callsTo(fetchMock, "/persons/p1", "GET")).toHaveLength(1);
    // Nothing changed yet: neither save nor cancel is offered as active.
    expect(within(form).getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(within(form).getByRole("button", { name: "Отмена" })).toBeDisabled();
  });

  it("shows email and birth date read-only, never as editable inputs", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();
    const form = await profileForm();

    expect(within(form).getByText("anna@example.com")).toBeInTheDocument();
    expect(within(form).getByText("4 марта 2001 г.")).toBeInTheDocument();
    expect(within(form).queryByRole("textbox", { name: /Email/i })).not.toBeInTheDocument();
    expect(within(form).queryByLabelText(/Дата рождения/)).not.toBeInTheDocument();
    expect(form.querySelector('input[type="email"], input[type="date"]')).toBeNull();
    const names = within(form)
      .getAllByRole("textbox")
      .map((input) => input.getAttribute("name"));
    expect(names.sort()).toEqual(["address", "first_name", "last_name", "middle_name", "phone"]);
  });

  it("saves only the changed allowed fields via PATCH and updates the header without reload", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.clear(within(form).getByLabelText("Фамилия"));
    await user.type(within(form).getByLabelText("Фамилия"), "Петрова");
    await user.type(within(form).getByLabelText("Отчество"), "Сергеевна");
    await user.clear(within(form).getByLabelText("Адрес"));
    await user.click(within(form).getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(callsTo(fetchMock, "/persons/p1", "PATCH")).toHaveLength(1));
    const [[url, init]] = callsTo(fetchMock, "/persons/p1", "PATCH");
    expect(url).toBe("/api/v1/persons/p1");
    expect(JSON.parse(String(init?.body))).toEqual({
      last_name: "Петрова",
      middle_name: "Сергеевна",
      address: null,
    });

    expect(await screen.findByText("Данные профиля сохранены")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /Петрова Анна/ }),
    ).toBeInTheDocument();
    const saved = await profileForm();
    expect(within(saved).getByLabelText("Фамилия")).toHaveValue("Петрова");
    expect(within(saved).getByLabelText("Адрес")).toHaveValue("");
    expect(within(saved).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("cancel restores the loaded values without a request", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.clear(within(form).getByLabelText("Имя"));
    await user.type(within(form).getByLabelText("Имя"), "Мария");
    await user.type(within(form).getByLabelText("Телефон"), "1");
    await user.click(within(form).getByRole("button", { name: "Отмена" }));

    expect(within(form).getByLabelText("Имя")).toHaveValue("Анна");
    expect(within(form).getByLabelText("Телефон")).toHaveValue("+79990000000");
    expect(callsTo(fetchMock, "/persons/p1", "PATCH")).toHaveLength(0);
  });

  it("validates required names client-side without sending PATCH", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.clear(within(form).getByLabelText("Имя"));
    await user.click(within(form).getByRole("button", { name: "Сохранить" }));

    expect(within(form).getByRole("alert")).toHaveTextContent("Укажите имя.");
    expect(callsTo(fetchMock, "/persons/p1", "PATCH")).toHaveLength(0);
  });

  it("blocks a second submit while saving", async () => {
    let release: (response: FakeResponse) => void = () => {};
    const fetchMock = fakeBackend(null, {
      patch: () => new Promise<FakeResponse>((resolve) => (release = resolve)),
    });
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.type(within(form).getByLabelText("Отчество"), "А");
    await user.click(within(form).getByRole("button", { name: "Сохранить" }));

    const saving = await within(form).findByRole("button", { name: "Сохранение…" });
    expect(saving).toBeDisabled();
    expect(within(form).getByRole("button", { name: "Отмена" })).toBeDisabled();
    expect(within(form).getByLabelText("Отчество")).toBeDisabled();
    fireEvent.submit(form);
    fireEvent.click(saving);
    expect(callsTo(fetchMock, "/persons/p1", "PATCH")).toHaveLength(1);

    await act(async () => release({ status: 500, body: { error: { code: "internal_error" } } }));
  });

  it("shows a backend validation error with the affected field and keeps the edits", async () => {
    fakeBackend(null, {
      patch: () => ({
        status: 422,
        body: {
          error: {
            code: "validation_error",
            message: "Request validation failed",
            details: { fields: [{ field: "phone", code: "string_too_long", message: "x" }] },
          },
        },
      }),
    });
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.type(within(form).getByLabelText("Телефон"), "9");
    await user.click(within(form).getByRole("button", { name: "Сохранить" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("Проверьте поля: Телефон.");
    expect(within(form).getByLabelText("Телефон")).toHaveValue("+799900000009");
    expect(screen.queryByText(/Request validation failed/)).not.toBeInTheDocument();
  });

  it("shows a refused (403) save and a network failure as user-facing errors", async () => {
    let attempt = 0;
    fakeBackend(null, {
      patch: () => {
        attempt += 1;
        if (attempt === 1) {
          return { status: 403, body: { error: { code: "forbidden", message: "Forbidden" } } };
        }
        throw new TypeError("Failed to fetch");
      },
    });
    renderSettingsWithMenu();
    const user = userEvent.setup();
    const form = await profileForm();

    await user.type(within(form).getByLabelText("Отчество"), "А");
    await user.click(within(form).getByRole("button", { name: "Сохранить" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "У вас нет прав на изменение этих данных.",
    );

    await user.click(within(form).getByRole("button", { name: "Сохранить" }));
    expect(await within(form).findByText(/Не удалось связаться с сервером/)).toBeInTheDocument();
  });

  it("offers no editable Person fields when the backend does not expose the own Person", async () => {
    fakeBackend(null, { personStatus: 404 });
    renderSettingsWithMenu();

    expect(
      await screen.findByText("Изменение данных профиля в настройках вам недоступно."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Данные профиля" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Фамилия")).not.toBeInTheDocument();
    // Photo management and the password section are unaffected.
    expect(screen.getByRole("button", { name: /Изменить фотографию/ })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Изменить пароль" })).toBeInTheDocument();
  });
});

// --- TH #311: password change -------------------------------------------------

async function fillPassword(current: string, next: string, confirm: string) {
  const user = userEvent.setup();
  const form = await screen.findByRole("form", { name: "Изменить пароль" });
  await user.type(within(form).getByLabelText("Текущий пароль"), current);
  await user.type(within(form).getByLabelText("Новый пароль"), next);
  await user.type(within(form).getByLabelText("Подтверждение нового пароля"), confirm);
  return { user, form };
}

describe("SettingsPage password change (TH #311)", () => {
  it("renders the security section with three password fields", async () => {
    fakeBackend(null);
    renderSettingsWithMenu();

    expect(await screen.findByRole("heading", { name: "Безопасность" })).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Изменить пароль" });
    for (const label of ["Текущий пароль", "Новый пароль", "Подтверждение нового пароля"]) {
      expect(within(form).getByLabelText(label)).toHaveAttribute("type", "password");
    }
    expect(within(form).getByLabelText("Текущий пароль")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    // Disabled until every field is filled.
    expect(within(form).getByRole("button", { name: "Изменить пароль" })).toBeDisabled();
  });

  it("does not send a request when the confirmation does not match", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("old-password", "new-password-1", "new-password-2");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));

    expect(within(form).getByRole("alert")).toHaveTextContent(
      "Новый пароль и подтверждение не совпадают.",
    );
    expect(callsTo(fetchMock, "/auth/password/change", "POST")).toHaveLength(0);
  });

  it("sends current and new password, shows success and clears the fields", async () => {
    const fetchMock = fakeBackend(null);
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("old-password", "new-password-1", "new-password-1");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));

    await waitFor(() => expect(callsTo(fetchMock, "/auth/password/change", "POST")).toHaveLength(1));
    const [[url, init]] = callsTo(fetchMock, "/auth/password/change", "POST");
    expect(url).toBe("/api/v1/auth/password/change");
    expect(JSON.parse(String(init?.body))).toEqual({
      current_password: "old-password",
      new_password: "new-password-1",
    });
    expect(await within(form).findByRole("status")).toHaveTextContent("Пароль изменён.");
    for (const label of ["Текущий пароль", "Новый пароль", "Подтверждение нового пароля"]) {
      expect(within(form).getByLabelText(label)).toHaveValue("");
    }
    // The session is left as the backend leaves it: no logout request.
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/auth/logout"))).toBe(
      false,
    );
  });

  it("shows an incorrect current password", async () => {
    fakeBackend(null, {
      passwordChange: () => ({
        status: 403,
        body: {
          error: { code: "incorrect_current_password", message: "Current password is incorrect" },
        },
      }),
    });
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("wrong", "new-password-1", "new-password-1");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Текущий пароль указан неверно.",
    );
    expect(screen.queryByText("Current password is incorrect")).not.toBeInTheDocument();
  });

  it("shows a weak new password", async () => {
    fakeBackend(null, {
      passwordChange: () => ({
        status: 422,
        body: {
          error: {
            code: "weak_password",
            message: "Password must be at least 8 characters long",
          },
        },
      }),
    });
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("old-password", "short", "short");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Новый пароль слишком простой.",
    );
  });

  it("shows a generic error for any other failure", async () => {
    fakeBackend(null, {
      passwordChange: () => ({ status: 500, body: { error: { code: "internal_error" } } }),
    });
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("old-password", "new-password-1", "new-password-1");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Не удалось изменить пароль. Попробуйте ещё раз.",
    );
  });

  it("blocks a second submit while the change is in flight", async () => {
    let release: (response: FakeResponse) => void = () => {};
    const fetchMock = fakeBackend(null, {
      passwordChange: () => new Promise<FakeResponse>((resolve) => (release = resolve)),
    });
    renderSettingsWithMenu();
    const { user, form } = await fillPassword("old-password", "new-password-1", "new-password-1");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));
    const saving = await within(form).findByRole("button", { name: "Сохранение…" });
    expect(saving).toBeDisabled();
    fireEvent.submit(form);
    expect(callsTo(fetchMock, "/auth/password/change", "POST")).toHaveLength(1);

    await act(async () => release({ status: 200, body: { status: "ok" } }));
  });

  it("never writes passwords to browser storage or the console", async () => {
    fakeBackend(null, {
      passwordChange: () => ({
        status: 403,
        body: { error: { code: "incorrect_current_password", message: "x" } },
      }),
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((level) =>
      vi.spyOn(console, level),
    );
    const client = createTestQueryClient();
    renderWithProviders(
      <>
        <ProfileMenu />
        <SettingsPage />
      </>,
      { route: "/settings", client },
    );
    const { user, form } = await fillPassword("secret-current", "secret-new-1", "secret-new-1");

    await user.click(within(form).getByRole("button", { name: "Изменить пароль" }));
    await within(form).findByRole("alert");
    // The settled request (and its variables) is gone from the shared cache.
    await waitFor(() =>
      expect(client.getMutationCache().getAll().map((m) => m.state.variables)).not.toContainEqual(
        expect.objectContaining({ current_password: "secret-current" }),
      ),
    );

    const leaked = (value: unknown) => /secret-(current|new-1)/.test(JSON.stringify(value) ?? "");
    expect(setItem.mock.calls.some(leaked)).toBe(false);
    for (const spy of consoleSpies) expect(spy.mock.calls.some(leaked)).toBe(false);
    expect(leaked({ ...localStorage })).toBe(false);
    expect(leaked({ ...sessionStorage })).toBe(false);
  });
});
