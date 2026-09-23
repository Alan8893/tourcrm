import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";

import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import styles from "./PhotoCropDialog.module.css";

/**
 * Round-viewport crop editor (AVATAR-PHOTO-SPEC.md §4): the user pans
 * (drag / arrow keys) and zooms (buttons / slider / wheel) the photo under
 * a fixed round viewport; everything outside the circle is dimmed. No
 * face detection, no automatic centering — the initial state is simply
 * the whole photo scaled to cover the circle, and the user decides the
 * composition.
 *
 * Save exports the square area circumscribing the circle (the stored
 * image is square, not a circular cut-out). The backend re-validates and
 * normalizes whatever is sent (profile-photo-api.md §2), so this export
 * is a transport of the user's composition, not the final stored form.
 */

/** Stage (square editing area) and round viewport, in CSS px. */
export const CROP_STAGE_SIZE = 300;
export const CROP_VIEWPORT_SIZE = 240;
const EXPORT_SIZE = 512;
const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;
const PAN_STEP = 10;

type Offset = { x: number; y: number };
type NaturalSize = { width: number; height: number };

function clampOffset(offset: Offset, natural: NaturalSize, scale: number): Offset {
  const maxX = Math.max(0, (natural.width * scale - CROP_VIEWPORT_SIZE) / 2);
  const maxY = Math.max(0, (natural.height * scale - CROP_VIEWPORT_SIZE) / 2);
  return {
    x: Math.min(maxX, Math.max(-maxX, offset.x)),
    y: Math.min(maxY, Math.max(-maxY, offset.y)),
  };
}

export type PhotoCropDialogProps = {
  file: File | null;
  saving?: boolean;
  onCancel: () => void;
  onSave: (photo: Blob) => void;
  onImageError: () => void;
};

export function PhotoCropDialog({ file, saving = false, onCancel, onSave, onImageError }: PhotoCropDialogProps) {
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [natural, setNatural] = useState<NaturalSize | null>(null);
  const [zoom, setZoom] = useState(MIN_ZOOM);
  const [offset, setOffset] = useState<Offset>({ x: 0, y: 0 });
  const imageRef = useRef<HTMLImageElement | null>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);

  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setSourceUrl(url);
    setNatural(null);
    setZoom(MIN_ZOOM);
    setOffset({ x: 0, y: 0 });
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const baseScale = natural
    ? Math.max(CROP_VIEWPORT_SIZE / natural.width, CROP_VIEWPORT_SIZE / natural.height)
    : 1;
  const scale = baseScale * zoom;

  function applyZoom(nextZoom: number) {
    const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoom));
    setZoom(clamped);
    if (natural) setOffset((current) => clampOffset(current, natural, baseScale * clamped));
  }

  function panBy(dx: number, dy: number) {
    if (!natural) return;
    setOffset((current) => clampOffset({ x: current.x + dx, y: current.y + dy }, natural, scale));
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    dragRef.current = { ...drag, x: event.clientX, y: event.clientY };
    panBy(dx, dy);
  }

  function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-PAN_STEP, 0],
      ArrowRight: [PAN_STEP, 0],
      ArrowUp: [0, -PAN_STEP],
      ArrowDown: [0, PAN_STEP],
    };
    const move = moves[event.key];
    if (move) {
      event.preventDefault();
      panBy(move[0], move[1]);
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      applyZoom(zoom + ZOOM_STEP);
    } else if (event.key === "-") {
      event.preventDefault();
      applyZoom(zoom - ZOOM_STEP);
    }
  }

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    applyZoom(zoom + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP));
  }

  function handleSave() {
    const image = imageRef.current;
    if (!image || !natural) return;
    const canvas = document.createElement("canvas");
    canvas.width = EXPORT_SIZE;
    canvas.height = EXPORT_SIZE;
    const context = canvas.getContext("2d");
    if (!context) {
      onImageError();
      return;
    }
    // Source rectangle (in natural image px) under the circle's bounding
    // square: the image centre sits at stage centre + offset.
    const sourceSize = CROP_VIEWPORT_SIZE / scale;
    const sourceX = natural.width / 2 - (CROP_VIEWPORT_SIZE / 2 + offset.x) / scale;
    const sourceY = natural.height / 2 - (CROP_VIEWPORT_SIZE / 2 + offset.y) / scale;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, EXPORT_SIZE, EXPORT_SIZE);
    context.drawImage(image, sourceX, sourceY, sourceSize, sourceSize, 0, 0, EXPORT_SIZE, EXPORT_SIZE);
    canvas.toBlob(
      (blob) => {
        if (blob) onSave(blob);
        else onImageError();
      },
      "image/webp",
      0.92,
    );
  }

  const displayWidth = natural ? natural.width * scale : 0;
  const displayHeight = natural ? natural.height * scale : 0;

  return (
    <Dialog
      open={file !== null}
      title="Фотография профиля"
      description="Перетащите фото и настройте масштаб, чтобы выбрать, что попадёт в круг."
      onClose={saving ? () => undefined : onCancel}
      actions={
        <>
          <Button variant="secondary" icon="action.cancel" onClick={onCancel} disabled={saving}>
            Отмена
          </Button>
          <Button variant="primary" icon="action.save" onClick={handleSave} disabled={!natural || saving}>
            Сохранить
          </Button>
        </>
      }
    >
      <div className={styles.editor}>
        <div
          className={styles.stage}
          style={{ width: CROP_STAGE_SIZE, height: CROP_STAGE_SIZE }}
          data-testid="photo-crop-stage"
          role="application"
          aria-label="Область кадрирования: перетащите фото или используйте стрелки"
          tabIndex={0}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onKeyDown={handleKeyDown}
          onWheel={handleWheel}
        >
          {sourceUrl ? (
            <img
              ref={imageRef}
              className={styles.image}
              src={sourceUrl}
              alt="Выбранная фотография"
              draggable={false}
              onLoad={(event) => {
                const { naturalWidth, naturalHeight } = event.currentTarget;
                if (!naturalWidth || !naturalHeight) {
                  onImageError();
                  return;
                }
                setNatural({ width: naturalWidth, height: naturalHeight });
              }}
              onError={onImageError}
              style={{
                width: displayWidth,
                height: displayHeight,
                transform: `translate(-50%, -50%) translate(${offset.x}px, ${offset.y}px)`,
                visibility: natural ? "visible" : "hidden",
              }}
            />
          ) : null}
          <div
            className={styles.viewport}
            data-testid="photo-crop-viewport"
            style={{ width: CROP_VIEWPORT_SIZE, height: CROP_VIEWPORT_SIZE }}
            aria-hidden="true"
          />
        </div>
        <div className={styles.zoomRow}>
          <Button
            variant="secondary"
            onClick={() => applyZoom(zoom - ZOOM_STEP)}
            disabled={!natural || zoom <= MIN_ZOOM}
            aria-label="Уменьшить"
          >
            −
          </Button>
          <input
            className={styles.zoomSlider}
            type="range"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={0.01}
            value={zoom}
            aria-label="Масштаб"
            disabled={!natural}
            onChange={(event) => applyZoom(Number(event.target.value))}
          />
          <Button
            variant="secondary"
            onClick={() => applyZoom(zoom + ZOOM_STEP)}
            disabled={!natural || zoom >= MAX_ZOOM}
            aria-label="Увеличить"
          >
            +
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
