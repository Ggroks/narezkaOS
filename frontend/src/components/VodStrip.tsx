import { useEffect, useRef, useState } from "react";

/**
 * Полоса VOD — миникарта всей записи.
 *
 * Стоит в одном и том же месте на этапах «моменты», «кадр», «стиль» и
 * «экспорт» и выглядит одинаково: меняется только подсветка выбранного.
 * Это единственный элемент, который отвечает на вопрос «где я в пятичасовой
 * записи», и он же связывает разные экраны в один продукт.
 *
 * Рисуется на canvas, а не элементами: на пятичасовой записи это шестьсот
 * столбцов чата плюс засечка на каждый момент. Сотни узлов в дереве браузер
 * пересчитывает при каждом наведении, canvas перерисовывается за один проход.
 */

export type Moment = {
  index: number;
  start: number;
  end: number;
  score: number;
  selected: boolean;
};

export type Timeline = {
  duration: number;
  chat: number[];
  moments: Moment[];
};

type Props = {
  data: Timeline | null;
  /** Текущее положение плеера, секунды. */
  playhead?: number;
  onSeek?: (seconds: number) => void;
  onPick?: (moment: Moment) => void;
  /** Кадр записи в заданной секунде — для подсказки при наведении. */
  frameUrl?: (at: number) => string;
};

const HEIGHT = 44;

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name);
  return value.trim() || fallback;
}

function formatAt(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function VodStrip({ data, playhead, onSeek, onPick, frameUrl }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<{ x: number; at: number } | null>(null);

  useEffect(() => {
    const node = canvas.current;
    if (!node || !data || data.duration <= 0) return;

    // Плотность экрана учитывается явно: без этого на ретине линии в один
    // пиксель выходят мыльными.
    const ratio = window.devicePixelRatio || 1;
    const width = node.clientWidth;
    node.width = Math.round(width * ratio);
    node.height = Math.round(HEIGHT * ratio);

    const ctx = node.getContext("2d");
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, HEIGHT);

    const chatColor = cssVar("--chat", "#9a94a6");
    const signal = cssVar("--signal", "#e8a33d");
    const faint = cssVar("--text-faint", "#66616f");
    const at = (seconds: number) => (seconds / data.duration) * width;

    // Слой 1: активность чата. Он идёт первым и приглушённым — это фон,
    // на котором читаются моменты, а не самостоятельные данные.
    if (data.chat.length) {
      ctx.fillStyle = chatColor;
      ctx.globalAlpha = 0.28;
      const step = width / data.chat.length;
      data.chat.forEach((value, i) => {
        const h = Math.max(1, value * (HEIGHT - 8));
        ctx.fillRect(i * step, HEIGHT - h, Math.max(1, step - 0.5), h);
      });
      ctx.globalAlpha = 1;
    }

    // Слой 2: моменты. Высота засечки — оценка, поэтому сильные видно сразу,
    // без чтения чисел.
    for (const moment of data.moments) {
      const x = at(moment.start);
      const h = Math.max(4, moment.score * (HEIGHT - 6));
      if (moment.selected) {
        ctx.fillStyle = signal;
        ctx.fillRect(x, HEIGHT - h, 2, h);
      } else {
        ctx.strokeStyle = faint;
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, HEIGHT - h + 0.5, 1, h - 1);
      }
    }

    // Слой 3: положение плеера.
    if (playhead != null) {
      ctx.fillStyle = cssVar("--text", "#eae7ef");
      ctx.fillRect(at(playhead), 0, 1, HEIGHT);
    }
  }, [data, playhead]);

  if (!data || data.duration <= 0) {
    return <div className="vod-strip empty">полоса появится после разбора записи</div>;
  }

  const secondsAt = (clientX: number, box: DOMRect) =>
    ((clientX - box.left) / box.width) * data.duration;

  return (
    <div className="vod-wrap">
      <canvas
        ref={canvas}
        className="vod-strip"
        style={{ height: HEIGHT }}
        role="slider"
        tabIndex={0}
        aria-label="Полоса записи"
        aria-valuemin={0}
        aria-valuemax={Math.round(data.duration)}
        aria-valuenow={Math.round(playhead ?? 0)}
        aria-valuetext={formatAt(playhead ?? 0)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover({ x: e.clientX - box.left, at: secondsAt(e.clientX, box) });
        }}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const at = secondsAt(e.clientX, box);
          // Клик рядом с моментом открывает его, а не просто перематывает:
          // засечки — главное, ради чего в полосу целятся.
          const near = data.moments.find((m) => Math.abs(m.start - at) < data.duration / 200);
          if (near && onPick) onPick(near);
          else onSeek?.(at);
        }}
        onKeyDown={(e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
          e.preventDefault();
          const step = e.shiftKey ? 300 : 30;
          const next = (playhead ?? 0) + (e.key === "ArrowRight" ? step : -step);
          onSeek?.(Math.max(0, Math.min(data.duration, next)));
        }}
      />

      {hover && (
        <div className="vod-tip" style={{ left: hover.x }}>
          {frameUrl && <img src={frameUrl(hover.at)} alt="" loading="lazy" />}
          <span className="tnum">{formatAt(hover.at)}</span>
        </div>
      )}
    </div>
  );
}
