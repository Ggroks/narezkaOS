import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  formatDuration,
  subscribeToJob,
  type JobEvent,
  type DetectorsInfo,
  type Timeline, type EncodersInfo,
  type Framing,
  type ModelsInfo,
  type ReviewClip,
  type ShortsIndex,
  type Transcript,
  type VideoDetail as Detail,
} from "../api";
import { FramingPanel } from "./FramingPanel";
import { PerformanceView } from "./PerformanceView";
import { VodStrip } from "./VodStrip";
import { SettingsPanel } from "./SettingsPanel";
import { PublishView } from "./PublishView";
import { ReviewView } from "./ReviewView";
import { ShortsView } from "./ShortsView";
import { TranscriptView } from "./TranscriptView";

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
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [encoders, setEncoders] = useState<EncodersInfo | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.video(videoId);
      setDetail(data);
      setRunning(data.job?.status === "running" || data.job?.status === "queued");
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
      api.timeline(videoId).then(setTimeline).catch(() => setTimeline(null));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [videoId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  async function start(stage?: string, force = false) {
    setError(null);
    setEvents([]);
    try {
      await api.run(videoId, { stage, force });
      setRunning(true);
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
        <button onClick={onBack}>← К списку</button>
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

  return (
    <>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <VodStrip data={timeline} frameUrl={(at) => api.frameUrl(videoId, at, 90)} />

      <div className="row" style={{ marginBottom: 16 }}>
        <div className="grow">
          <div style={{ fontWeight: 600 }}>{meta.source_title || meta.source_file || videoId}</div>
          <div className="small dim mono">{videoId}</div>
        </div>
        <button className="primary" disabled={running} onClick={() => start()}>
          {running ? "Обработка идёт…" : "Запустить обработку"}
        </button>
        <button disabled={running} onClick={() => start(undefined, true)} title="Игнорировать кэш">
          Пересчитать
        </button>
      </div>

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

      <details className="section" aria-labelledby="stages-heading">
        <summary id="stages-heading">Стадии обработки</summary>
        <div className="section-body">
        <div className="stages">
          {detail.stages.map((stage) => {
            const event = [...events].reverse().find((e) => e.stage === stage.name && e.event === "finished");
            const outcome = event?.outcome;
            const isActive = activeStage === stage.name;
            return (
              <div key={stage.name} className={`stage${isActive ? " active" : ""}`}>
                <div className="name mono">{stage.name}</div>
                <div className="grow small dim">{stage.description}</div>
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
                  <span className="small dim tnum">{stage.duration.toFixed(1)} с</span>
                )}
                <button
                  className="icon"
                  disabled={running}
                  aria-label={`Запустить только стадию ${stage.name}`}
                  onClick={() => start(stage.name, true)}
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
      </details>

      {/* Обзор идёт до кадрирования: сначала решают, годится ли момент,
          и только потом — как его показать. */}
      <div className="editor">
        <div className="editor-main">
          <ReviewView videoId={videoId} durationSeconds={meta.duration_seconds ?? null} />
        </div>

        <aside className="editor-side" aria-label="Настройки ролика">
          {framing && (
            <div className="panel">
              <h3>Что вошло в ролик</h3>
              <SettingsPanel
                value={framing}
                onChange={updateFraming}
                splitAvailable={splitAvailable}
                disabled={running}
                // Отбор моментов идёт до сборки, поэтому пересчитывать надо
                // с него: перерендерить старые кандидаты бессмысленно.
                models={models}
                detectors={detectors}
                encoders={encoders}
                onReanalyse={() => start("candidates", true)}
              />
              <button
                className="primary"
                style={{ inlineSize: "100%", marginBlockStart: 10 }}
                disabled={running}
                onClick={() => start("render", true)}
              >
                Пересобрать ролики
              </button>
            </div>
          )}
        </aside>
      </div>

      {/* Кадрирование убрано под сворачивание: настраивают его один раз
          на видео, а переключатели справа трогают постоянно. Держать рядом
          две панели настроек — значит заставлять выбирать между ними. */}
      {meta.has_video !== false && (
        <details className="section">
          <summary>Кадрирование и предпросмотр</summary>
          <div className="section-body">
            <FramingPanel
              videoId={videoId}
              busy={running}
              // Кадрирование входит в ключ кэша, поэтому пересчёта всего
              // пайплайна не нужно — достаточно перерендерить ролики.
              onSaved={() => start("render", true)}
            />
          </div>
        </details>
      )}

      {shorts && shorts.files.length > 0 && <ShortsView videoId={videoId} shorts={shorts} />}

      <PublishView videoId={videoId} />

      <PerformanceView videoId={videoId} clips={reviewClips} />

      {transcript && (
        <details className="section">
          <summary>Транскрипт</summary>
          <div className="section-body">
            <TranscriptView transcript={transcript} onSeek={seek} currentTime={currentTime} />
          </div>
        </details>
      )}
    </>
  );
}
