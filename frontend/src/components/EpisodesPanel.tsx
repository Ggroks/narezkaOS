import { useCallback, useEffect, useState } from "react";
import { api, formatDuration, type CompilationsInfo, type EpisodesInfo } from "../api";

/**
 * Длинная нарезка: что собрать и из чего.
 *
 * Сюжет и подборка — не выбор одного из двух: можно оба, одно или ничего.
 * Эпизодов тоже берётся столько, сколько нужно, — каждый даёт свой ролик.
 * Выбирать один за человека незачем: он видит список с названиями и сам
 * знает, что из этого стоит смотреть.
 *
 * **Выбор сохраняется на сервере.** До этого галочки жили только в памяти
 * вкладки: человек их ставил, а стадия собирала по умолчанию из общего
 * конфига — то есть не собирала вовсе, потому что по умолчанию она
 * выключена. Теперь выбор пишется при записи и он же включает стадию:
 * отдельного переключателя «собирать длинную нарезку» нет, он был бы
 * вторым выключателем к той же лампе.
 */

type Props = {
  videoId: string;
  /** Идёт ли обработка — пока идёт, менять выбор нельзя. */
  busy?: boolean;
  /**
   * Наверх уходят две вещи, на которые смотрит кнопка запуска: выбрано ли
   * хоть что-нибудь и сколько нарезок уже собрано. Кнопка стоит над панелью,
   * а состояние живёт здесь — иначе то же самое пришлось бы грузить дважды.
   */
  onState?: (chosen: boolean, made: number) => void;
};

export function EpisodesPanel({ videoId, busy, onState }: Props) {
  const [info, setInfo] = useState<EpisodesInfo | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [story, setStory] = useState(true);
  const [best, setBest] = useState(false);
  const [minutes, setMinutes] = useState(20);
  const [made, setMade] = useState<CompilationsInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .episodes(videoId)
      .then((data) => {
        if (!alive) return;
        setInfo(data);
        setStory(data.story);
        setBest(data.best);
        setMinutes(data.target_minutes ?? 20);
        setPicked(new Set(data.selected ?? []));
      })
      .catch(() => setInfo(null));
    api
      .compilations(videoId)
      .then((data) => alive && setMade(data))
      .catch(() => setMade(null));
    return () => {
      alive = false;
    };
  }, [videoId]);

  // Выбор уходит на сервер сразу, а не по кнопке «сохранить»: кнопка,
  // которая ничего не делает, кроме запоминания, — лишний шаг между
  // намерением и результатом.
  const save = useCallback(
    async (next: { story: boolean; best: boolean; picked: Set<number>; minutes: number }) => {
      onState?.(next.story || next.best, made?.files.length ?? 0);
      try {
        await api.setCompilation(videoId, {
          story: next.story,
          best: next.best,
          episodes: [...next.picked].sort((a, b) => a - b),
          target_minutes: next.minutes,
        });
        setError(null);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [videoId, onState, made],
  );

  useEffect(() => {
    // Первое сообщение наверх — из загруженного состояния, чтобы кнопка
    // запуска знала, выбрано ли что-нибудь, ещё до первого нажатия.
    if (info) onState?.(info.story || info.best, made?.files.length ?? 0);
  }, [info, made, onState]);

  if (!info) return null;

  const total = info.episodes.length;
  const allPicked = total > 0 && picked.size === total;

  const setAll = (next: Set<number>) => {
    setPicked(next);
    void save({ story, best, picked: next, minutes });
  };

  return (
    <>
      {made && made.files.length > 0 && (
        <section className="card">
          <h2>Готовые нарезки ({made.files.length})</h2>
          <div className="made">
            {/* Готовые нарезки идут первыми: человек пришёл смотреть
                результат, а не настраивать следующий прогон. */}
            {made.files.map((item) => (
              <figure key={item.file} className="made-item">
                <video controls preload="metadata" src={api.compilationUrl(videoId, item.file)} />
                <figcaption>
                  <b>{item.title}</b>
                  {item.summary && <span className="small dim">{item.summary}</span>}
                  <span className="small dim tnum">
                    {item.kind === "best" ? "подборка" : "эпизод"} ·{" "}
                    {Math.round(item.duration / 60)} мин · {item.pieces} кусков ·{" "}
                    {(item.size_bytes / 1024 ** 2).toFixed(0)} МБ
                  </span>
                  <a href={api.compilationUrl(videoId, item.file)} download>
                    скачать
                  </a>
                  {item.chapters_text && (
                    /* Оглавление копируют целиком в описание под видео,
                       поэтому оно даётся одним блоком, а не списком. */
                    <details className="chapters">
                      <summary className="small">
                        Таймкоды ({item.chapters_text.split("\n").length})
                      </summary>
                      <pre>{item.chapters_text}</pre>
                      <button
                        type="button"
                        onClick={() => navigator.clipboard?.writeText(item.chapters_text ?? "")}
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
      )}

      <section className="card">
        <h2>Что собрать</h2>

        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}

        <label className="toggle">
          <input
            type="checkbox"
            checked={best}
            disabled={busy}
            onChange={(e) => {
              setBest(e.target.checked);
              void save({ story, best: e.target.checked, picked, minutes });
            }}
          />
          <span className="toggle-body">
            <b>Подборка лучших моментов</b>
            <span className="small dim">
              Яркие места со всей записи: зацепка, середина по порядку, кульминация
            </span>
          </span>
        </label>

        <label className="toggle">
          <input
            type="checkbox"
            checked={story}
            disabled={busy}
            onChange={(e) => {
              setStory(e.target.checked);
              void save({ story: e.target.checked, best, picked, minutes });
            }}
          />
          <span className="toggle-body">
            <b>Связные эпизоды</b>
            <span className="small dim">
              Занятие с началом и концом, уплотнённое до нужной длины. Границы
              ищет модель по расшифровке — это отдельная работа, и она идёт
              только под эту галочку
            </span>
          </span>
        </label>

        <label className="choice" style={{ marginTop: 12 }}>
          <span className="choice-label">Желаемая длина, минут</span>
          <input
            type="number"
            min={1}
            max={180}
            value={minutes}
            disabled={busy}
            onChange={(e) => setMinutes(Number(e.target.value))}
            onBlur={() => void save({ story, best, picked, minutes })}
          />
          <span className="choice-hint">
            Ориентир, а не жёсткая рамка: нарезка режется по границам моментов,
            и точная длина зависит от них
          </span>
        </label>

        {story && total > 0 && (
          <>
            <div className="row small dim" style={{ margin: "16px 0 8px" }}>
              <span className="grow">
                Выбрано {picked.size} из {total} — каждый эпизод даёт свой ролик
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setAll(allPicked ? new Set() : new Set(info.episodes.map((_, i) => i)))
                }
              >
                {allPicked ? "снять все" : "выбрать все"}
              </button>
            </div>

            <div className="episodes">
              {info.episodes.map((episode, index) => (
                <label key={index} className={`episode${picked.has(index) ? " picked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={picked.has(index)}
                    disabled={busy}
                    onChange={() => {
                      const next = new Set(picked);
                      if (next.has(index)) next.delete(index);
                      else next.add(index);
                      setAll(next);
                    }}
                  />
                  <span className="episode-body">
                    <span className="episode-title">{episode.title}</span>
                    {episode.summary && <span className="small dim">{episode.summary}</span>}
                    <span className="small dim tnum">
                      {formatDuration(episode.start)} → {formatDuration(episode.end)} ·{" "}
                      {Math.round(episode.duration / 60)} мин · цельность{" "}
                      {episode.coherence.toFixed(2)}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </>
        )}

        {story && total === 0 && (
          <p className="small dim" style={{ marginTop: 12 }}>
            {info.reason === "эпизоды ещё не размечены"
              ? "Эпизоды найдутся при сборке — их ищет модель по расшифровке."
              : info.reason ??
                "Связных занятий в этой записи не нашлось — подойдёт подборка лучших моментов"}
          </p>
        )}
      </section>
    </>
  );
}
