import { useCallback, useEffect, useRef, useState } from "react";
import { PreviewSlot } from "./PreviewSlot";
import { Timeline } from "./Timeline";
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

  /**
   * Начало куска относительно записи. Плеер играет вырезанный момент,
   * а границы правятся во времени записи — значит одно надо переводить
   * в другое. Запас должен совпадать с серверным `PREVIEW_LEAD`.
   */
  const lead = 4;

  /**
   * Границы куска, который загружен в плеер. Берутся один раз на момент и
   * не следуют за подрезкой.
   *
   * Иначе каждая правка перезагружала кусок, а вместе с ним ехала и шкала:
   * ручка после тяги возвращалась на прежнее место, потому что дорожка
   * отсчитывалась от новой границы. Выглядело это как «подрезка не
   * сработала», хотя границы менялись.
   */
  const [base, setBase] = useState<{ index: number; start: number; end: number } | null>(null);

  useEffect(() => {
    if (!clip) return;
    setBase((was) =>
      was && was.index === clip.index
        ? was
        : {
            index: clip.index,
            // С запасом на обе стороны от того, что предлагала автоматика:
            // подрезать можно и внутрь, и наружу.
            start: Math.min(clip.start, clip.original?.start ?? clip.start),
            end: Math.max(clip.end, clip.original?.end ?? clip.end),
          },
    );
  }, [clip]);

  const span = base && base.index === clip?.index ? base : clip;
  const offset = span ? Math.max(span.start - lead, 0) : 0;

  const seekTo = useCallback(
    (seconds: number, play = false) => {
      const element = videoRef.current;
      if (!element) return;
      element.currentTime = Math.max(seconds - offset, 0);
      setPlayhead(offset + element.currentTime);
      if (play) void element.play();
    },
    [offset],
  );

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
      const at = offset + element.currentTime;
      setPlayhead(at);
      if (at >= clip.end) {
        element.pause();
        element.currentTime = Math.max(clip.end - offset, 0);
      }
    };
    element.addEventListener("timeupdate", onTime);
    return () => element.removeEventListener("timeupdate", onTime);
  }, [clip, offset]);

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
    async (edge: "start" | "end", moment?: number) => {
      if (!clip || !videoRef.current) return;
      // Время записи, а не время внутри вырезанного куска: плеер играет
      // фрагмент, начинающийся с `offset`, а границы хранятся в секундах
      // записи. Без слагаемого начало момента на 15-й минуте уезжало
      // на четвёртую секунду записи.
      const at = Number((moment ?? offset + videoRef.current.currentTime).toFixed(2));
      try {
        setReview(await api.setReview(videoId, clip.index, { [edge]: at }));
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [videoId, clip, offset],
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
        <p className="empty">Моменты ещё не найдены. Запустите обработку — они появятся после разбора звука и речи.</p>
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
          <span className="badge warn">модель ещё не оценивала этот момент</span>
        )}
      </div>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <div className="review-player">
        {/* Плеер переезжает в полосу предпросмотра слева, а управление
            остаётся здесь: это тот же самый элемент, по которому двигают
            границы момента, и разрывать его с кнопками нельзя. */}
        <PreviewSlot>
          <video
            ref={videoRef}
            src={span ? api.reviewMediaUrl(videoId, span.index, span.start, span.end) : undefined}
            preload="metadata"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={() => clip && seekTo(clip.start)}
            // Позиция во времени записи, а не куска: по ней правятся границы.
            onTimeUpdate={(e) => setPlayhead(offset + e.currentTarget.currentTime)}
          />
        </PreviewSlot>

        {clip && (
          <>
            {/* Перемотка и подрезка — тягой по дорожке, как в любом
                видеоредакторе. Рядом кнопок «Начало здесь» и «Конец здесь»
                не было видно ни где ты находишься, ни куда двигаешь
                границу: чтобы подрезать начало, приходилось сперва доиграть
                до нужного кадра. */}
            <div className="transport">
              <button
                className="transport-play"
                onClick={togglePlay}
                aria-label={playing ? "Пауза" : "Играть"}
                title="Пробел"
              >
                <span aria-hidden="true">{playing ? "❚❚" : "▶"}</span>
              </button>
              <span className="small dim tnum transport-time">
                {formatDuration(playhead)} <span className="dim">/ {formatDuration(clip.end)}</span>
              </span>
              <Timeline
                from={offset}
                to={Math.max((span?.end ?? clip.end) + lead, offset + 1)}
                position={playhead}
                onSeek={(at) => seekTo(at)}
                clipStart={clip.start}
                clipEnd={clip.end}
                onTrim={(edge, at) => void setBound(edge, at)}
                label="Момент: положение и границы"
              />
              <span className="small dim tnum transport-length">
                {(clip.end - clip.start).toFixed(0)} с
              </span>
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
              {/* Одна кнопка снимает всё решение целиком — и оценку, и
                  подрезанные границы. Пока она смотрела только на оценку,
                  вернуть сдвинутую границу было нечем: подрезал — и живи
                  с этим. Название говорит, что именно снимется. */}
              <button
                onClick={() => void clearVerdict()}
                disabled={clip.verdict === null && !clip.edited}
              >
                {clip.verdict === null ? "Вернуть границы" : "Снять оценку"}
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
