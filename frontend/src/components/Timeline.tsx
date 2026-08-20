import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Дорожка времени: перемотка тягой и, если нужно, границы отрезка.
 *
 * До неё перемотка была рядом кнопок — «Играть», «С начала момента», «Начало
 * здесь», «Конец здесь». Кнопкой нельзя ни отмотать на нужное место, ни
 * увидеть, где ты находишься; чтобы подрезать начало, приходилось сначала
 * доигрывать до нужного кадра, а потом нажимать «Начало здесь». В любом
 * видеоредакторе это делается тягой по дорожке, и ожидание у человека
 * ровно такое.
 *
 * Компонент один на два места: обзор моментов (там есть границы) и выбор
 * кадра для предпросмотра (там их нет). Две дорожки со своей арифметикой
 * времени разъехались бы — как уже разъехались когда-то предпросмотр
 * и сборка.
 *
 * Время снаружи всегда в секундах записи, а не отрезка: по нему правятся
 * границы и по нему же перематывается плеер.
 */

type Props = {
  /** Границы видимой дорожки — обычно отрезок с запасом по краям. */
  from: number;
  to: number;
  /** Где сейчас воспроизведение. */
  position: number;
  onSeek: (at: number) => void;
  /** Границы отрезка. Заданы — у дорожки появляются ручки подрезки. */
  clipStart?: number;
  clipEnd?: number;
  onTrim?: (edge: "start" | "end", at: number) => void;
  disabled?: boolean;
  /** Подпись для программ чтения с экрана. */
  label?: string;
};

/** Что тянут прямо сейчас. */
type Drag = "playhead" | "start" | "end" | null;

export function Timeline({
  from, to, position, onSeek, clipStart, clipEnd, onTrim, disabled, label,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag>(null);
  // Пока тянут, показывается местное значение: ждать ответа плеера значит
  // видеть, как ручка отстаёт от пальца.
  const [dragAt, setDragAt] = useState<number | null>(null);

  const span = Math.max(to - from, 0.001);
  const share = (at: number) => Math.min(Math.max((at - from) / span, 0), 1);
  const trimmable = clipStart != null && clipEnd != null && onTrim != null;

  const atPointer = useCallback(
    (clientX: number) => {
      const box = trackRef.current?.getBoundingClientRect();
      if (!box) return from;
      const ratio = Math.min(Math.max((clientX - box.left) / box.width, 0), 1);
      return from + ratio * span;
    },
    [from, span],
  );

  // Тяга слушается на окне, а не на дорожке: палец уходит за её край, и
  // без этого перемотка обрывалась бы на середине движения.
  useEffect(() => {
    if (!drag) return;
    const move = (event: PointerEvent) => {
      const at = atPointer(event.clientX);
      setDragAt(at);
      if (drag === "playhead") onSeek(at);
    };
    const done = (event: PointerEvent) => {
      const at = atPointer(event.clientX);
      if (drag === "playhead") onSeek(at);
      else if (trimmable) onTrim?.(drag, at);
      setDrag(null);
      setDragAt(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", done);
    window.addEventListener("pointercancel", done);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", done);
      window.removeEventListener("pointercancel", done);
    };
  }, [drag, atPointer, onSeek, onTrim, trimmable]);

  const startDrag = (what: Exclude<Drag, null>) => (event: React.PointerEvent) => {
    if (disabled) return;
    event.preventDefault();
    event.stopPropagation();
    setDrag(what);
    setDragAt(atPointer(event.clientX));
  };

  const shown = (edge: "start" | "end", value: number) =>
    drag === edge && dragAt != null ? dragAt : value;

  const head = drag === "playhead" && dragAt != null ? dragAt : position;
  const left = trimmable ? shown("start", clipStart!) : from;
  const right = trimmable ? shown("end", clipEnd!) : to;

  return (
    <div
      ref={trackRef}
      className={`timeline${disabled ? " disabled" : ""}`}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label ?? "Позиция воспроизведения"}
      aria-valuemin={from}
      aria-valuemax={to}
      aria-valuenow={Number(head.toFixed(2))}
      onPointerDown={(event) => {
        if (disabled) return;
        onSeek(atPointer(event.clientX));
        setDrag("playhead");
      }}
      onKeyDown={(event) => {
        // С клавиатуры — то же, что тягой: шаг секунда, с Shift — десять.
        const step = event.shiftKey ? 10 : 1;
        if (event.key === "ArrowLeft") onSeek(Math.max(head - step, from));
        else if (event.key === "ArrowRight") onSeek(Math.min(head + step, to));
        else return;
        event.preventDefault();
      }}
    >
      {trimmable && (
        <>
          {/* Отрезок внутри дорожки: что войдёт в ролик. Вне его дорожка
              приглушена — видно, сколько записи остаётся за границами. */}
          <div
            className="timeline-clip"
            style={{ insetInlineStart: `${share(left) * 100}%`, inlineSize: `${(share(right) - share(left)) * 100}%` }}
          />
          <button
            type="button"
            className="timeline-handle start"
            style={{ insetInlineStart: `${share(left) * 100}%` }}
            aria-label="Начало момента"
            disabled={disabled}
            onPointerDown={startDrag("start")}
          />
          <button
            type="button"
            className="timeline-handle end"
            style={{ insetInlineStart: `${share(right) * 100}%` }}
            aria-label="Конец момента"
            disabled={disabled}
            onPointerDown={startDrag("end")}
          />
        </>
      )}

      <div className="timeline-played" style={{ inlineSize: `${share(head) * 100}%` }} />
      <div
        className="timeline-head"
        style={{ insetInlineStart: `${share(head) * 100}%` }}
        onPointerDown={startDrag("playhead")}
      />
    </div>
  );
}
