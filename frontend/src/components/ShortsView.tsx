import { useEffect, useState } from "react";
import { Hint } from "./Hint";
import { api, formatDuration, type PublishEntry, type ShortsIndex } from "../api";

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
  // Тексты держатся целиком, а не одними заголовками: они принадлежат
  // ролику, и разносить их по разным экранам значит заставлять человека
  // сверять номера.
  const [texts, setTexts] = useState<Record<number, PublishEntry>>({});

  useEffect(() => {
    let alive = true;
    api
      .publish(videoId)
      .then((data) => {
        if (!alive) return;
        setTexts(Object.fromEntries(data.clips.map((c) => [c.index, c])));
      })
      .catch(() => setTexts({})); // текстов ещё нет — это нормальный ход работы
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
              {texts[file.index] ? (
                <span className="short-title">{texts[file.index].title}</span>
              ) : (
                <span className="short-title dim">Ролик {file.index + 1}</span>
              )}
              {/* Подробности под значком, а не строкой: пять чисел подряд
                  под каждым роликом превращают сетку в таблицу, а нужны они
                  редко — когда выбирают между двумя похожими. */}
              <span className="short-facts">
                <b className="tnum">{file.duration.toFixed(0)} с</b>
                {file.interest_score != null && (
                  <span className="tnum">оценка {file.interest_score.toFixed(2)}</span>
                )}
                <Hint side="top">
                  <b>Откуда взят:</b> {formatDuration(file.start)} → {formatDuration(file.end)}
                  <br />
                  <b>Длительность:</b> {file.duration.toFixed(0)} с
                  <br />
                  <b>Вес файла:</b> {(file.size_bytes / 1024 ** 2).toFixed(1)} МБ
                  {file.interest_score != null && (
                    <>
                      <br />
                      <b>Оценка модели:</b> {file.interest_score.toFixed(2)}
                      {file.rank != null && ` (место ${file.rank})`}
                    </>
                  )}
                  {file.explanation && (
                    <>
                      <br />
                      <b>Почему выбран:</b> {file.explanation}
                    </>
                  )}
                </Hint>
              </span>
              <a
                href={`/api/videos/${videoId}/shorts/${file.index}/media`}
                download
                aria-label={`Скачать ролик ${file.index + 1}`}
              >
                скачать
              </a>

              {texts[file.index] && (
                /* Свёрнуто по умолчанию: описание с хэштегами занимает больше
                   места, чем сам ролик, и разворачивают его только когда
                   собираются публиковать. */
                <details className="short-text">
                  <summary className="small">Текст для публикации</summary>
                  <textarea
                    readOnly
                    rows={6}
                    value={texts[file.index].ready}
                    onFocus={(e) => e.currentTarget.select()}
                  />
                  <button
                    type="button"
                    onClick={() =>
                      navigator.clipboard?.writeText(texts[file.index].ready)
                    }
                  >
                    скопировать
                  </button>
                </details>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
