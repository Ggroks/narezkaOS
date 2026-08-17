import { useEffect, useState } from "react";
import { api, formatDuration, type CompilationsInfo, type EpisodesInfo } from "../api";

/**
 * Выбор того, что собрать в длинную нарезку.
 *
 * Сюжет и подборка — не выбор одного из двух: можно оба, одно или ничего.
 * Эпизодов тоже берётся столько, сколько нужно, — каждый даёт свой ролик.
 * Выбирать один за человека незачем: он видит список с названиями и сам
 * знает, что из этого стоит смотреть.
 */

type Props = { videoId: string };

export function EpisodesPanel({ videoId }: Props) {
  const [info, setInfo] = useState<EpisodesInfo | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [story, setStory] = useState(true);
  const [best, setBest] = useState(false);
  const [made, setMade] = useState<CompilationsInfo | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .episodes(videoId)
      .then((data) => {
        if (!alive) return;
        setInfo(data);
        setStory(data.story);
        setBest(data.best);
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

  if (!info) return null;

  const toggle = (index: number) => {
    setPicked((was) => {
      const next = new Set(was);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const total = info.episodes.length;
  const allPicked = total > 0 && picked.size === total;

  return (
    <section className="card">
      <h2>Длинная нарезка</h2>

      {made && made.files.length > 0 && (
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
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      <label className="toggle">
        <input type="checkbox" checked={best} onChange={(e) => setBest(e.target.checked)} />
        <span className="toggle-body">
          <b>Подборка лучших моментов</b>
          <span className="small dim">
            Яркие места со всей записи: зацепка, середина по порядку, кульминация
          </span>
        </span>
      </label>

      <label className="toggle">
        <input type="checkbox" checked={story} onChange={(e) => setStory(e.target.checked)} />
        <span className="toggle-body">
          <b>Связные эпизоды</b>
          <span className="small dim">
            Занятие с началом и концом, уплотнённое до нужной длины
          </span>
        </span>
      </label>

      {story && total > 0 && (
        <>
          <div className="row small dim" style={{ margin: "12px 0 8px" }}>
            <span className="grow">
              Выбрано {picked.size} из {total} — каждый эпизод даёт свой ролик
            </span>
            <button
              type="button"
              onClick={() =>
                setPicked(allPicked ? new Set() : new Set(info.episodes.map((_, i) => i)))
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
                  onChange={() => toggle(index)}
                />
                <span className="episode-body">
                  <span className="episode-title">{episode.title}</span>
                  {episode.summary && (
                    <span className="small dim">{episode.summary}</span>
                  )}
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
        <p className="small dim">
          {info.reason ??
            "Связных занятий в этой записи не нашлось — подойдёт подборка лучших моментов"}
        </p>
      )}
    </section>
  );
}
