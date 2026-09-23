import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SettingsPage } from "./SettingsPage";
import { CROP_VIEWPORT_SIZE } from "./PhotoCropDialog";
import { ProfileMenu } from "../shell/ProfileMenu";
import { renderWithProviders } from "../test/renderWithProviders";

/** Stateful fake backend: `/auth/me` reflects PUT/DELETE photo, so the
 * tests observe the real cache update + refetch path. */
function fakeBackend(initialPhotoFileId: string | null) {
  let photoFileId = initialPhotoFileId;
  const me = () => ({
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
        photo_file_id: photoFileId,
      },
    },
    role_assignments: [],
  });
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.endsWith("/auth/me")) {
      return new Response(JSON.stringify(me()), { status: 200 });
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
