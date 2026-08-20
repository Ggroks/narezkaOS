import { useEffect, useState } from "react";
import { Hint } from "./Hint";
import { PreviewSlot } from "./PreviewSlot";
import { api, formatDuration, type PublishEntry, type ShortsIndex } from "../api";

type Props = {
  videoId: string;
  shorts: ShortsIndex;
  /** Идёт обработка — пересобирать сейчас нечего, машина занята. */
  busy?: boolean;
  /** Пересборка встала в очередь: наверху пора показать ход работы. */
  onQueued?: () => void;
  /** Занимать ли полосу предпросмотра: она одна на экран. */
  preview?: boolean;
  /**
   * Где сейчас остановлен ролик: номер и секунда от его начала. По этому
   * месту настраивается кадр — человек ставит паузу на нужном кадре и правит
   * рамку, глядя именно на него.
   */
  onFrame?: (index: number, offset: number) => void;
};

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
export function ShortsView({
  videoId, shorts, busy, onQueued, preview = true, onFrame,
}: Props) {
  /**
   * Пересборка одного ролика.
   *
   * Полная пересборка тридцати роликов — десятки минут, а поправить обычно
   * надо один: у него не сработала детекция лица или подвинуты границы.
   * Остальные при этом остаются прежними — об этом сказано в подсказке,
   * потому что молчаливый разнобой хуже долгого ожидания.
   */
  // Какой ролик открыт в полосе предпросмотра. Первый — чтобы полоса не
  // пустовала: человек пришёл на эту вкладку смотреть, а не выбирать.
  const [playing, setPlaying] = useState(0);
  const [queued, setQueued] = useState<number | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  async function rerender(index: number) {
    setFailed(null);
    try {
      await api.rerenderShort(videoId, index);
      setQueued(index);
      onQueued?.();
    } catch (exc) {
      // Сюда приходит и отказ «запись уже в очереди»: причина написана
      // словами, и показать её честнее, чем притвориться, что поставили.
      setFailed(exc instanceof Error ? exc.message : String(exc));
      setQueued(null);
    }
  }

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
      {failed && (
        <div className="error" role="alert">
          {failed}
        </div>
      )}
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

      {/* Смотреть — в полосе слева, одним плеером. Тридцать плееров в сетке
          браузер держал одновременно: каждый тянул свои метаданные, и на
          записи с тридцатью роликами это заметно и по памяти, и по сети.
          А вертикальный ролик в ячейке сетки всё равно выходил крошечным. */}
      <PreviewSlot active={preview}>
        <video
          controls
          preload="metadata"
          key={playing}
          aria-label={`Ролик ${playing + 1}`}
          // Пауза и перемотка задают кадр для настроек. На каждом кадре
          // воспроизведения — не нужно: настраивают по остановленному.
          onPause={(e) => onFrame?.(playing, e.currentTarget.currentTime)}
          onSeeked={(e) => onFrame?.(playing, e.currentTarget.currentTime)}
          onLoadedMetadata={() => onFrame?.(playing, 0)}
          // «#t=0.1» — чтобы в полосе стоял первый кадр, а не чёрный
          // прямоугольник: без метки времени браузер не рисует ничего,
          // пока ролик не запустят.
          src={`/api/videos/${videoId}/shorts/${playing}/media#t=0.1`}
        />
      </PreviewSlot>

      <div className="shorts-grid">
        {shorts.files.map((file) => (
          <figure
            key={file.index}
            className={`short${file.index === playing ? " current" : ""}`}
          >
            <button
              type="button"
              className="short-open"
              aria-label={`Смотреть ролик ${file.index + 1}`}
              aria-pressed={file.index === playing}
              onClick={() => setPlaying(file.index)}
            >
              <span className="short-number tnum">{file.index + 1}</span>
              <span className="short-duration tnum">{file.duration.toFixed(0)} с</span>
            </button>
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
              {/* Два действия в одну строку: по отдельности каждое
                  выглядит важнее, чем есть. */}
              <span className="short-actions small">
                <a
                  href={`/api/videos/${videoId}/shorts/${file.index}/media`}
                  download
                  aria-label={`Скачать ролик ${file.index + 1}`}
                >
                  скачать
                </a>
                <button
                  className="linkish"
                  disabled={busy || queued === file.index}
                  title="Собрать заново только этот ролик с нынешними настройками. Остальные останутся прежними"
                  onClick={() => void rerender(file.index)}
                >
                  {queued === file.index ? "в очереди…" : "пересобрать"}
                </button>
              </span>

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
