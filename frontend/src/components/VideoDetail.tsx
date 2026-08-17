import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  formatDuration,
  subscribeToJob,
  type JobEvent,
  type DetectorsInfo, type EncodersInfo,
  type Framing,
  type ModelsInfo,
  type ReviewClip,
  type ShortsIndex,
  type StageGroup,
  type Transcript,
  type VideoDetail as Detail,
} from "../api";
import { FramingPanel } from "./FramingPanel";
import { PerformanceView } from "./PerformanceView";
import { EpisodesPanel } from "./EpisodesPanel";
import { Hint } from "./Hint";
import { RunPanel } from "./RunPanel";
import { Workspace, type Tab, type TabId } from "./Workspace";
import { AnalysisSettings, ShortSettings } from "./SettingsPanel";
import { PublishView } from "./PublishView";
import { ReviewView } from "./ReviewView";
import { ShortsView } from "./ShortsView";
import { TranscriptView } from "./TranscriptView";
import { STAGE_TITLES } from "../stages";

type Props = { videoId: string; onBack: () => void };

const OUTCOME_BADGE: Record<string, string> = {
  done: "ok",
  cached: "",
  skipped: "warn",
  failed: "bad",
};

const OUTCOME_LABEL: Record<string, string> = {
  done: "выполнено",
  cached: "из кэша",
  skipped: "пропущено",
  failed: "ошибка",
};

export function VideoDetail({ videoId, onBack }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [shorts, setShorts] = useState<ShortsIndex | null>(null);
  // Список кандидатов нужен и обзору, и контуру сбора данных: второй берёт
  // из него, какие клипы ещё не отмечены опубликованными.
  const [reviewClips, setReviewClips] = useState<ReviewClip[]>([]);
  const [framing, setFraming] = useState<Framing | null>(null);
  const [splitAvailable, setSplitAvailable] = useState(false);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [detectors, setDetectors] = useState<DetectorsInfo | null>(null);
  const [encoders, setEncoders] = useState<EncodersInfo | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [running, setRunning] = useState(false);
  // Выбрано ли что-нибудь для длинной нарезки. Живёт здесь, потому что от
  // этого зависит кнопка запуска, а она стоит над панелью выбора.
  const [longChosen, setLongChosen] = useState(false);
  const [longMade, setLongMade] = useState(0);
  // Вкладка помнится между заходами: возвращаясь к проекту, человек
  // продолжает с того места, где остановился, а не с начала.
  const [tab, setTab] = useState<TabId>(
    () => (localStorage.getItem("tab") as TabId) || "source",
  );

  useEffect(() => {
    localStorage.setItem("tab", tab);
  }, [tab]);

  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.video(videoId);
      setDetail(data);
      // Только «выполняется»: ожидание в очереди — не работа, и показывать
      // его как идущую обработку значит врать про то, что происходит.
      setRunning(data.job?.status === "running");
      if (data.job?.events?.length) setEvents(data.job.events);
      try {
        setTranscript(await api.transcript(videoId));
      } catch {
        setTranscript(null); // транскрипта ещё нет — это нормально
      }
      try {
        setShorts(await api.shorts(videoId));
      } catch {
        setShorts(null); // роликов ещё нет
      }
      try {
        setReviewClips((await api.review(videoId)).clips);
      } catch {
        setReviewClips([]); // кандидатов ещё нет
      }
      try {
        const state = await api.framing(videoId);
        setFraming(state.current);
        // Сплит предлагается только там, где вебка найдена наложением:
        // включать раскладку, для которой нет данных, — обещать несбыточное.
        setSplitAvailable(state.split_available ?? false);
      } catch {
        setFraming(null);
      }
      // Списки грузятся мягко: каталог моделей ходит в сеть, и его отказ
      // не должен мешать остальной работе.
      api.models().then(setModels).catch(() => setModels(null));
      api.detectors().then(setDetectors).catch(() => setDetectors(null));
      api.encoders().then(setEncoders).catch(() => setEncoders(null));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [videoId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Пока задача ждёт очереди, событий нет и подписываться не на что:
  // место в очереди меняется от чужой работы. Раз в три секунды хватает.
  useEffect(() => {
    if (!detail?.queue_position) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [detail?.queue_position, load]);

  // Живой поток событий, пока задача активна (§69).
  useEffect(() => {
    if (!running) return;
    const lastSeq = events.length ? events[events.length - 1].seq : 0;
    const unsubscribe = subscribeToJob(videoId, lastSeq, (event) => {
      setEvents((previous) => [...previous, event]);
      if (event.event === "job_finished" || event.event === "pipeline_finished") {
        setRunning(false);
        void load();
      }
    });
    return unsubscribe;
    // events намеренно не в зависимостях: пересоздание потока на каждом
    // событии оборвало бы подписку.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, videoId, load]);

  async function updateFraming(patch: Partial<Framing>) {
    if (!framing) return;
    const next = { ...framing, ...patch };
    setFraming(next);
    try {
      await api.setFraming(videoId, next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  /**
   * Запуск куска работы.
   *
   * Недостающее подтягивается на сервере, а посчитанное берётся из кэша,
   * поэтому нажать «не ту» кнопку нельзя: сборка роликов на необработанной
   * записи сама сделает разбор, а на обработанной не станет делать его заново.
   */
  async function start(payload: { group?: StageGroup; stage?: string; force?: boolean }) {
    setError(null);
    setEvents([]);
    try {
      await api.run(videoId, payload);
      setRunning(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function stop() {
    try {
      await api.stop(videoId);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  function seek(seconds: number) {
    const element = videoRef.current;
    if (!element) return;
    element.currentTime = seconds;
    void element.play();
  }

  if (!detail) {
    return (
      <div className="card">
        {error ? (
          <div className="error" role="alert">
            {error}
          </div>
        ) : (
          <p className="empty">Загрузка…</p>
        )}
        <button onClick={onBack}>← К проектам</button>
      </div>
    );
  }

  const meta = detail.metadata ?? {};
  const activeStage = running
    ? [...events].reverse().find((e) => e.event === "started")?.stage
    : undefined;
  // Последняя отметка о ходе работы. Без неё долгая стадия выглядит зависшей:
  // отбор на пятичасовой записи молчит три четверти часа.
  const progress = running
    ? [...events].reverse().find((e) => e.event === "progress" && e.stage === activeStage)
    : undefined;

  // Чем кончился прошлый прогон. Сломанное и пропущенное разведены: поломка
  // это новость всегда, а пропуск — только когда результата так и нет.
  // Иначе на каждой записи без чата висело бы «чтение чата пропущено».
  const finished = running ? [] : [...events].reverse();
  const failed = finished.find(
    (e) => e.event === "error" || (e.event === "finished" && e.outcome === "failed"),
  );
  const skipped = finished.find((e) => e.event === "finished" && e.outcome === "skipped");

  const failure = failed
    ? failed.event === "error"
      ? `Ошибка: ${failed.message}`
      : `${STAGE_TITLES[failed.stage] ?? failed.stage} — ошибка${failed.reason ? `: ${failed.reason}` : ""}`
    : undefined;
  const skipNote = skipped
    ? `${STAGE_TITLES[skipped.stage] ?? skipped.stage} — пропущено${skipped.reason ? `: ${skipped.reason}` : ""}`
    : undefined;

  // Сколько моментов уйдёт в ролики. Считается так же, как решает сборка:
  // взятые моделью минус отклонённые человеком плюс возвращённые им.
  // Раньше здесь стояло «все кандидаты минус отклонённые» и выходило «соберём
  // 79», когда модель отобрала тридцать.
  const scored = reviewClips.some((clip) => clip.selected);
  const rejected = reviewClips.filter(
    (clip) => clip.verdict === "reject" && (!scored || clip.selected),
  ).length;
  const rescued = scored
    ? reviewClips.filter((clip) => !clip.selected && clip.verdict === "accept").length
    : 0;
  const forShorts = scored
    ? reviewClips.filter(
        (clip) =>
          (clip.selected && clip.verdict !== "reject") ||
          (!clip.selected && clip.verdict === "accept"),
      ).length
    : reviewClips.filter((clip) => clip.verdict !== "reject").length;

  const tabs: Tab[] = [
    { id: "source", label: "Исходник" },
    { id: "moments", label: "Обзор моментов", count: reviewClips.length },
    { id: "shorts", label: "Короткие видео", count: shorts?.files.length ?? 0 },
    { id: "long", label: "Длинная нарезка" },
    { id: "results", label: "Результаты" },
  ];

  const runShared = {
    running,
    queued: detail.queue_position,
    activeStage,
    progress,
    onStop: stop,
    failure,
    note: skipNote,
  };

  return (
    <Workspace
      tabs={tabs}
      active={tab}
      onSelect={setTab}
      // Своё название важнее взятого из источника — как и в каталоге.
      title={meta.title || meta.source_title || meta.source_file || videoId}
      subtitle={formatDuration(meta.duration_seconds)}
      actions={
        <button className="ghost" onClick={onBack}>
          К проектам
        </button>
      }
      stages={
      <div className="stages-panel">
        <h3>Ход работы</h3>
        <div className="section-body">
        <div className="stages">
          {detail.stages.map((stage) => {
            const event = [...events].reverse().find((e) => e.stage === stage.name && e.event === "finished");
            const outcome = event?.outcome;
            const isActive = activeStage === stage.name;
            return (
              <div key={stage.name} className={`stage${isActive ? " active" : ""}`}>
                {/* Название человеческое, пояснение — под значком: описание
                    стадии длинное, и в узкой колонке оно ломало строку. */}
                <span className="stage-name">
                  {STAGE_TITLES[stage.name] ?? stage.name}
                  <Hint>{stage.description}</Hint>
                </span>
                {isActive && progress?.total ? (
                  <span className="badge run tnum" title={progress.note ?? undefined}>
                    {progress.done} из {progress.total}
                  </span>
                ) : (
                  isActive && (
                    <span className="badge run">
                      {progress?.note ?? "выполняется…"}
                    </span>
                  )
                )}
                {!isActive && outcome && (
                  <span className={`badge ${OUTCOME_BADGE[outcome] ?? ""}`}>
                    {OUTCOME_LABEL[outcome] ?? outcome}
                  </span>
                )}
                {!isActive && !outcome && stage.status === "done" && (
                  <span className="badge ok">выполнено</span>
                )}
                {stage.duration != null && (
                  <span className="small dim tnum stage-time">
                    {stage.duration >= 60
                      ? `${Math.round(stage.duration / 60)} мин`
                      : `${stage.duration.toFixed(0)} с`}
                  </span>
                )}
                <button
                  className="icon"
                  disabled={running}
                  aria-label={`Выполнить заново: ${STAGE_TITLES[stage.name] ?? stage.name}`}
                  onClick={() => start({ stage: stage.name, force: true })}
                >
                  <span aria-hidden="true">▶</span>
                </button>
              </div>
            );
          })}
        </div>

        {events.length > 0 && (
          <div className="log" style={{ marginTop: 12 }} role="log" aria-live="polite" aria-label="Журнал обработки">
            {events.map((event) => (
              <div key={event.seq}>
                <span className="dim">{event.at.slice(11, 19)}</span>{" "}
                <span style={{ color: "var(--accent)" }}>{event.stage}</span>{" "}
                {event.event === "started" && "запущена"}
                {event.event === "finished" && (OUTCOME_LABEL[event.outcome ?? ""] ?? event.outcome)}
                {event.event === "pipeline_started" && "пайплайн запущен"}
                {event.event === "pipeline_finished" && "пайплайн завершён"}
                {event.event === "job_finished" && `задача: ${event.status}`}
                {event.event === "error" && <span style={{ color: "var(--bad)" }}> {event.message}</span>}
                {event.reason && <span className="dim"> — {event.reason}</span>}
                {event.duration ? <span className="dim"> ({event.duration} с)</span> : null}
              </div>
            ))}
          </div>
        )}
        </div>
      </div>
      }
    >
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      {/* ── Исходник: по чему искать моменты и кнопка разбора ───────────── */}
      {tab === "source" && (
        <>
          <RunPanel
            {...runShared}
            title="Разбор записи"
            hint="Скачает запись, расшифрует речь и найдёт места, из которых выйдут ролики. Сами ролики пока не собираются"
            label="Найти моменты"
            againLabel="Найти заново"
            done={reviewClips.length > 0}
            onRun={() => start({ group: "analysis" })}
          />

          <section className="card source-preview" aria-labelledby="source-heading">
            <h2 id="source-heading">Исходник</h2>
            <video
              ref={videoRef}
              controls
              preload="metadata"
              src={api.mediaUrl(videoId)}
              onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            />
            <div className="row wrap small dim" style={{ marginTop: 10, gap: 14 }}>
              <span className="tnum">{formatDuration(meta.duration_seconds)}</span>
              {meta.video?.width && (
                <span className="tnum">
                  {meta.video.width}×{meta.video.height} @ {meta.video.fps} fps
                </span>
              )}
              {meta.audio?.codec && <span>звук: {meta.audio.codec}</span>}
              {meta.size_bytes && <span className="tnum">{(meta.size_bytes / 1024 ** 3).toFixed(2)} ГБ</span>}
            </div>
          </section>

          {/* Свёрнуты, когда моменты уже найдены, и открыты, когда ещё нет:
              то же правило, что на вкладке роликов. Настройки нужны, пока
              настраиваешь, и мешают, когда смотришь результат. */}
          {framing && (
            <details
              className="section"
              key={reviewClips.length > 0 ? "с-моментами" : "пусто"}
              open={reviewClips.length === 0}
            >
              <summary>Как искать моменты: сигналы, звук, модель</summary>
              <div className="section-body">
                <div className="panel">
                  <AnalysisSettings
                    value={framing}
                    onChange={updateFraming}
                    disabled={running}
                    models={models}
                  />
                </div>
              </div>
            </details>
          )}

          {transcript && (
            <TranscriptView transcript={transcript} currentTime={currentTime} onSeek={seek} />
          )}
        </>
      )}

      {/* ── Обзор моментов ─────────────────────────────────────────────── */}
      {tab === "moments" && (
        <ReviewView videoId={videoId} durationSeconds={meta.duration_seconds ?? null} />
      )}

      {/* ── Короткие видео: кадр, настройки ролика и сборка ─────────────── */}
      {tab === "shorts" && (
        <>
          <RunPanel
            {...runShared}
            title="Сборка коротких роликов"
            label="Собрать"
            againLabel="Пересобрать"
            hint={
              reviewClips.length === 0
                ? "Разбор запустится сам, а из найденных моментов соберутся ролики"
                : [
                    `Соберём ${forShorts} ${forShorts === 1 ? "момент" : "моментов"}`,
                    rejected > 0 ? `${rejected} отклонено вами` : "",
                    rescued > 0 ? `${rescued} возвращено вами` : "",
                  ]
                    .filter(Boolean)
                    .join(" · ")
            }
            done={(shorts?.files.length ?? 0) > 0}
            blocked={forShorts === 0 && reviewClips.length > 0 ? "Все моменты отклонены в обзоре — собирать нечего" : undefined}
            onRun={() => start({ group: "shorts" })}
          />

          {/* Пока роликов нет, настройки открыты: человек пришёл настраивать.
              Когда ролики есть — свёрнуты, потому что пришёл он смотреть их,
              а не листать мимо двух десятков переключателей. Ключ заставляет
              блок пересобраться при смене условия: иначе `open` осталось бы
              от первого показа, когда роликов ещё не было. */}
          <details
            className="section"
            key={(shorts?.files.length ?? 0) > 0 ? "с-роликами" : "пусто"}
            open={(shorts?.files.length ?? 0) === 0}
          >
            <summary>Настройки ролика: кадр, субтитры, звук</summary>
            <div className="section-body settings-column">
              {/* Предпросмотр рядом с настройками: их правят, глядя на
                  результат, а не вслепую с переходом туда-обратно. */}
              <FramingPanel videoId={videoId} busy={running} onSaved={() => void load()} />
              {framing && (
                <div className="panel">
                  <ShortSettings
                    value={framing}
                    onChange={updateFraming}
                    splitAvailable={splitAvailable}
                    disabled={running}
                    detectors={detectors}
                    encoders={encoders}
                  />
                </div>
              )}
            </div>
          </details>

          {shorts && shorts.files.length > 0 && <ShortsView videoId={videoId} shorts={shorts} />}
        </>
      )}

      {/* ── Длинная нарезка ────────────────────────────────────────────── */}
      {tab === "long" && (
        <>
          <RunPanel
            {...runShared}
            title="Длинная нарезка"
            hint="Соберёт выбранное ниже. Связные эпизоды сначала ищет модель по расшифровке — это отдельная работа, и она идёт только под свою галочку"
            label="Собрать"
            againLabel="Пересобрать"
            done={longMade > 0}
            blocked={longChosen ? undefined : "Отметьте ниже, что собирать: подборку, эпизоды или оба"}
            onRun={() => start({ group: "long" })}
          />
          <EpisodesPanel
            videoId={videoId}
            busy={running}
            onState={(chosen, made) => {
              setLongChosen(chosen);
              setLongMade(made);
            }}
          />
        </>
      )}

      {/* ── Результаты ─────────────────────────────────────────────────── */}
      {tab === "results" && (
        <>
          <PublishView videoId={videoId} />
          <PerformanceView videoId={videoId} clips={reviewClips} />
        </>
      )}
    </Workspace>
  );
}
