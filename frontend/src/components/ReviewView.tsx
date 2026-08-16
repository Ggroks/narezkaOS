import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, formatDuration, type Review, type ReviewClip, type Verdict } from "../api";

/** Оценка красится по смыслу, а не градиентом: три ступени читаются быстрее. */
function scoreClass(score: number | null | undefined): string {
  if (score == null) return "";
  if (score >= 0.6) return " good";
  if (score >= 0.35) return " fair";
  return " weak";
}

type Props = { videoId: string; durationSeconds: number | null };

const FACTOR_LABEL: Record<string, string> = {
  semantic: "смысл",
  emotion: "эмоция",
  audio: "звук",
  context: "контекст",
  completeness: "законченность",
  novelty: "новизна",
  visual: "картинка",
};

/** На сколько отматывают J и L. Пять секунд — шаг, на котором ещё виден контекст. */
const SEEK_STEP = 5;

const SHORTCUTS: [string, string][] = [
  ["Space / K", "пуск и пауза"],
  ["J / L", "−5 с / +5 с"],
  ["← / →", "предыдущий / следующий"],
  ["[ / ]", "начало / конец здесь"],
  ["A / R", "годится / не годится"],
  ["Backspace", "снять оценку"],
];

/**
 * Обзор кандидатов ([§35](BAZA.md#35), [§63](BAZA.md#63)).
 *
 * Экран решает одну задачу: посмотреть все отобранные моменты и сказать по
 * каждому «годится» или «нет», ни разу не дождавшись рендера. Поэтому здесь
 * играет **исходник** с переходом на нужный отрезок, а не готовые ролики:
 * рендер минуты видео занимает минуту, перемотка — мгновение.
 *
 * Каждое решение — обучающая метка (§63). Она копится сама собой при обычной
 * работе, в отличие от метрик публикации, которые приходят через недели.
 */
export function ReviewView({ videoId, durationSeconds }: Props) {
  const [review, setReview] = useState<Review | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const rootRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    try {
      setReview(await api.review(videoId));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [videoId]);

  useEffect(() => {
    void load();
  }, [load]);

  const clips = review?.clips ?? [];
  const clip: ReviewClip | undefined = clips[active];

  const seekTo = useCallback((seconds: number, play = false) => {
    const element = videoRef.current;
    if (!element) return;
    element.currentTime = Math.max(seconds, 0);
    setPlayhead(element.currentTime);
    if (play) void element.play();
  }, []);

  const select = useCallback(
    (index: number, play = false) => {
      const target = clips[index];
      if (!target) return;
      setActive(index);
      seekTo(target.start, play);
    },
    [clips, seekTo],
  );

  // Дойдя до конца отрезка, плеер останавливается: иначе он уезжает в соседний
  // материал и оценивать становится нечего.
  useEffect(() => {
    const element = videoRef.current;
    if (!element || !clip) return;
    const onTime = () => {
      setPlayhead(element.currentTime);
      if (element.currentTime >= clip.end) {
        element.pause();
        element.currentTime = clip.end;
      }
    };
    element.addEventListener("timeupdate", onTime);
    return () => element.removeEventListener("timeupdate", onTime);
  }, [clip]);

  const decide = useCallback(
    async (verdict: Verdict) => {
      if (!clip) return;
      try {
        setReview(await api.setReview(videoId, clip.index, { verdict }));
        // Переход к следующему неразмеченному: так тридцать кандидатов
        // проходятся подряд, без возврата к списку после каждого.
        const next = clips.findIndex((c, i) => i > active && c.verdict === null);
        if (next >= 0) select(next);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [videoId, clip, clips, active, select],
  );

  const setBound = useCallback(
    async (edge: "start" | "end") => {
      if (!clip || !videoRef.current) return;
      const at = Number(videoRef.current.currentTime.toFixed(2));
      try {
        setReview(await api.setReview(videoId, clip.index, { [edge]: at }));
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [videoId, clip],
  );

  const clearVerdict = useCallback(async () => {
    if (!clip) return;
    try {
      setReview(await api.clearReview(videoId, clip.index));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [videoId, clip]);

  const togglePlay = useCallback(() => {
    const element = videoRef.current;
    if (!element) return;
    if (element.paused) void element.play();
    else element.pause();
  }, []);

  // Клавиши работают, пока фокус внутри экрана обзора: глобальный обработчик
  // перехватывал бы ввод в полях на других карточках страницы.
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select")) return;

      const actions: Record<string, () => void> = {
        " ": togglePlay,
        k: togglePlay,
        л: togglePlay,
        j: () => seekTo((videoRef.current?.currentTime ?? 0) - SEEK_STEP),
        о: () => seekTo((videoRef.current?.currentTime ?? 0) - SEEK_STEP),
        l: () => seekTo((videoRef.current?.currentTime ?? 0) + SEEK_STEP, true),
        д: () => seekTo((videoRef.current?.currentTime ?? 0) + SEEK_STEP, true),
        ArrowLeft: () => select(active - 1, true),
        ArrowRight: () => select(active + 1, true),
        "[": () => void setBound("start"),
        х: () => void setBound("start"),
        "]": () => void setBound("end"),
        ъ: () => void setBound("end"),
        a: () => void decide("accept"),
        ф: () => void decide("accept"),
        r: () => void decide("reject"),
        к: () => void decide("reject"),
        Backspace: () => void clearVerdict(),
      };

      const action = actions[event.key] ?? actions[event.key.toLowerCase()];
      if (!action) return;
      event.preventDefault();
      action();
    }

    root.addEventListener("keydown", onKey);
    return () => root.removeEventListener("keydown", onKey);
  }, [active, select, seekTo, togglePlay, setBound, decide, clearVerdict]);

  const progress = useMemo(() => {
    if (!clip) return 0;
    const span = clip.end - clip.start;
    if (span <= 0) return 0;
    return Math.min(Math.max((playhead - clip.start) / span, 0), 1);
  }, [clip, playhead]);

  if (error && !review) {
    return (
      <section className="card">
        <h2>Обзор моментов</h2>
        <div className="error" role="alert">
          {error}
        </div>
      </section>
    );
  }

  if (!review) {
    return (
      <section className="card">
        <h2>Обзор моментов</h2>
        <p className="empty">Загрузка…</p>
      </section>
    );
  }

  if (!clips.length) {
    return (
      <section className="card">
        <h2>Обзор моментов</h2>
        <p className="empty">Кандидатов нет — запустите стадию candidates.</p>
      </section>
    );
  }

  const { stats } = review;

  return (
    <section
      className="card review"
      aria-labelledby="review-heading"
      ref={rootRef}
      tabIndex={-1}
    >
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <h2 id="review-heading" style={{ margin: 0 }}>
          Обзор моментов
        </h2>
        <span className="small dim tnum grow">
          {stats.accepted} годится · {stats.rejected} нет · {stats.undecided} без оценки
          {stats.edited > 0 && ` · ${stats.edited} с правкой границ`}
        </span>
        {stats.scored === 0 && (
          <span className="badge warn">модель не оценивала — запустите llm_select</span>
        )}
      </div>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <div className="review-player">
        <video
          ref={videoRef}
          src={api.mediaUrl(videoId)}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onLoadedMetadata={() => clip && seekTo(clip.start)}
          onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
        />

        {clip && (
          <>
            {/* Полоса отрезка: где идёт воспроизведение внутри клипа, а не
                внутри всей записи — судить приходится именно об отрезке. */}
            <div
              className="review-track"
              role="img"
              aria-label={`Позиция внутри момента: ${Math.round(progress * 100)}%`}
            >
              <div className="review-track-fill" style={{ inlineSize: `${progress * 100}%` }} />
            </div>

            <div className="row wrap review-controls">
              <button onClick={togglePlay} aria-label={playing ? "Пауза" : "Играть"}>
                {playing ? "Пауза" : "Играть"}
              </button>
              <button onClick={() => seekTo(clip.start, true)}>С начала момента</button>
              <span className="small dim tnum grow">
                {formatDuration(playhead)} из {formatDuration(clip.end)}
              </span>
              <button onClick={() => void setBound("start")} title="Клавиша [">
                Начало здесь
              </button>
              <button onClick={() => void setBound("end")} title="Клавиша ]">
                Конец здесь
              </button>
            </div>

            <div className="row wrap review-verdict">
              <button
                className={clip.verdict === "accept" ? "primary" : ""}
                onClick={() => void decide("accept")}
                title="Клавиша A"
              >
                Годится
              </button>
              <button
                className={clip.verdict === "reject" ? "danger-on" : ""}
                onClick={() => void decide("reject")}
                title="Клавиша R"
              >
                Не годится
              </button>
              <button onClick={() => void clearVerdict()} disabled={clip.verdict === null}>
                Снять оценку
              </button>
              {clip.edited && clip.original && (
                <span className="small dim tnum">
                  автоматика предлагала {formatDuration(clip.original.start)} →{" "}
                  {formatDuration(clip.original.end)}
                </span>
              )}
            </div>

            {clip.interest_score != null && (
              <div className="review-verdict-model">
                <span className={`review-score${scoreClass(clip.interest_score)}`}>
                  {clip.interest_score.toFixed(2)}
                </span>
                <span className="grow">
                  <span className="small dim">оценка модели · ранг {clip.rank}</span>
                  {clip.explanation && <p className="review-explanation">{clip.explanation}</p>}
                  {/* Просевшая оценка должна быть объяснена: иначе человек
                      видит низкий балл у хорошего момента и не понимает,
                      почему. Порог половины ролика — с него музыка перестаёт
                      быть случайным фоном. */}
                  {(clip.penalties?.music_present ?? 0) > 0.5 && (
                    <p className="review-warning">
                      В ролике играет музыка. Площадка может узнать её и закрыть
                      доступ к ролику или забрать доход с него — если права на
                      музыку не ваши, лучше выбрать другой момент.
                    </p>
                  )}
                </span>
              </div>
            )}

            {clip.factors && (
              <dl className="review-factors small">
                {Object.entries(clip.factors).map(([name, value]) => (
                  <div key={name} className={value == null ? "unmeasured" : ""}>
                    <dt>{FACTOR_LABEL[name] ?? name}</dt>
                    {/* null — «не измеряли», и это не то же самое, что ноль (§54). */}
                    <dd className="tnum">{value == null ? "не измерено" : value.toFixed(2)}</dd>
                  </div>
                ))}
              </dl>
            )}

            {clip.text && <p className="review-text small">{clip.text}</p>}
          </>
        )}
      </div>

      <ol className="review-list">
        {clips.map((item, index) => (
          <li key={item.index}>
            <button
              className={`review-card${index === active ? " active" : ""}${
                item.verdict ? ` ${item.verdict}` : ""
              }`}
              onClick={() => select(index, true)}
              aria-current={index === active}
            >
              <img
                className="review-thumb"
                src={api.frameUrl(videoId, item.peak_at ?? item.start)}
                alt=""
                loading="lazy"
              />
              <span className="review-card-body">
                <span className="row">
                  <span className="grow tnum">
                    {formatDuration(item.start)} → {formatDuration(item.end)}
                  </span>
                  <span className="small dim tnum">{item.duration.toFixed(0)} с</span>
                </span>
                <span className="small dim tnum">
                  {item.interest_score != null ? (
                    <>
                      <b className={`review-score-inline${scoreClass(item.interest_score)}`}>
                        {item.interest_score.toFixed(2)}
                      </b>{" "}
                      · ранг {item.rank}
                    </>
                  ) : (
                    <>сигнал {item.provisional_score?.toFixed(2) ?? "—"}</>
                  )}
                </span>
                {item.explanation && (
                  <span className="small dim review-card-why">{item.explanation}</span>
                )}
                <span className="review-badges">
                  {item.verdict === "accept" && <span className="badge ok">годится</span>}
                  {item.verdict === "reject" && <span className="badge bad">не годится</span>}
                  {item.verdict === null && <span className="badge">без оценки</span>}
                  {item.edited && <span className="badge warn">границы правлены</span>}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>

      <dl className="review-keys small dim">
        {SHORTCUTS.map(([keys, meaning]) => (
          <div key={keys}>
            <dt>
              <kbd>{keys}</kbd>
            </dt>
            <dd>{meaning}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
