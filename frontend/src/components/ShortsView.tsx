import { useEffect, useState } from "react";
import { api, formatDuration, type ShortsIndex } from "../api";

type Props = { videoId: string; shorts: ShortsIndex };

const BACKGROUND_LABEL: Record<string, string> = {
  blur: "размытый кадр",
  sharp: "кадр без размытия",
  solid: "однотонный",
  none: "кадр заполнен целиком",
};

/**
 * Готовые вертикальные ролики.
 *
 * Это единственный экран, где видно конечный продукт, поэтому ролики
 * показываются в реальной пропорции 9:16 — так же, как их увидит зритель,
 * включая то, не залезли ли субтитры в зону интерфейса платформы (§60).
 */
export function ShortsView({ videoId, shorts }: Props) {
  // Заголовок показывается на самом ролике, а не только в отдельном разделе:
  // ролик и его название — одно и то же, разносить их по экрану незачем.
  const [titles, setTitles] = useState<Record<number, string>>({});

  useEffect(() => {
    let alive = true;
    api
      .publish(videoId)
      .then((data) => {
        if (!alive) return;
        setTitles(Object.fromEntries(data.clips.map((c) => [c.index, c.title])));
      })
      .catch(() => setTitles({})); // текстов ещё нет — это нормальный ход работы
    return () => {
      alive = false;
    };
  }, [videoId]);

  return (
    <section className="card" aria-labelledby="shorts-heading">
      <h2 id="shorts-heading">Готовые ролики ({shorts.files.length})</h2>
      <div className="row wrap small dim" style={{ marginBottom: 12, gap: 14 }}>
        <span className="tnum">
          {shorts.width}×{shorts.height}
        </span>
        <span>фон: {BACKGROUND_LABEL[shorts.background] ?? shorts.background}</span>
        {/* С какими настройками рамки это отрендерено: после правки видно,
            относится показанное к новым настройкам или ещё к старым. */}
        {shorts.framing && <span>{shorts.framing.summary}</span>}
        {shorts.source === "candidates" && (
          <span className="badge warn">без отбора моделью</span>
        )}
      </div>

      <div className="shorts-grid">
        {shorts.files.map((file) => (
          <figure key={file.index} className="short">
            <video
              controls
              preload="metadata"
              aria-label={`Ролик ${file.index + 1}`}
              src={`/api/videos/${videoId}/shorts/${file.index}/media`}
            />
            <figcaption className="small dim">
              {titles[file.index] ? (
                <span className="short-title">{titles[file.index]}</span>
              ) : (
                <span className="short-title dim">Ролик {file.index + 1}</span>
              )}
              {file.interest_score != null && (
                <span title={file.explanation ?? undefined}>
                  оценка <b className="tnum">{file.interest_score.toFixed(2)}</b>
                  {file.rank != null && ` · ранг ${file.rank}`}
                </span>
              )}
              <span>
                {formatDuration(file.start)} → {formatDuration(file.end)}
              </span>
              <span>{file.duration.toFixed(0)} с</span>
              <span>{(file.size_bytes / 1024 ** 2).toFixed(1)} МБ</span>
              <a
                href={`/api/videos/${videoId}/shorts/${file.index}/media`}
                download
                aria-label={`Скачать ролик ${file.index + 1}`}
              >
                скачать
              </a>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
