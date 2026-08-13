import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  formatDuration,
  subscribeToJob,
  type JobEvent,
  type ShortsIndex,
  type Transcript,
  type VideoDetail as Detail,
} from "../api";
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
        {error ? <div className="error">{error}</div> : <div className="empty">Загрузка…</div>}
        <button onClick={onBack}>← К списку</button>
      </div>
    );
  }

  const meta = detail.metadata ?? {};
  const activeStage = running
    ? [...events].reverse().find((e) => e.event === "started")?.stage
    : undefined;

  return (
    <>
      {error && <div className="error">{error}</div>}

      <div className="row" style={{ marginBottom: 16 }}>
        <button onClick={onBack}>← К списку</button>
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

      <div className="card">
        <h2>Исходник</h2>
        <video
          ref={videoRef}
          controls
          preload="metadata"
          src={api.mediaUrl(videoId)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        />
        <div className="row wrap small dim" style={{ marginTop: 10, gap: 14 }}>
          <span>{formatDuration(meta.duration_seconds)}</span>
          {meta.video?.width && (
            <span>
              {meta.video.width}×{meta.video.height} @ {meta.video.fps} fps
            </span>
          )}
          {meta.audio?.codec && <span>звук: {meta.audio.codec}</span>}
          {meta.size_bytes && <span>{(meta.size_bytes / 1024 ** 3).toFixed(2)} ГБ</span>}
        </div>
      </div>

      <div className="card">
        <h2>Стадии</h2>
        <div className="stages">
          {detail.stages.map((stage) => {
            const event = [...events].reverse().find((e) => e.stage === stage.name && e.event === "finished");
            const outcome = event?.outcome;
            const isActive = activeStage === stage.name;
            return (
              <div key={stage.name} className={`stage${isActive ? " active" : ""}`}>
                <div className="name mono">{stage.name}</div>
                <div className="grow small dim">{stage.description}</div>
                {isActive && <span className="badge run">выполняется…</span>}
                {!isActive && outcome && (
                  <span className={`badge ${OUTCOME_BADGE[outcome] ?? ""}`}>
                    {OUTCOME_LABEL[outcome] ?? outcome}
                  </span>
                )}
                {!isActive && !outcome && stage.status === "done" && (
                  <span className="badge ok">выполнено</span>
                )}
                {stage.duration != null && <span className="small dim">{stage.duration.toFixed(1)} с</span>}
                <button disabled={running} onClick={() => start(stage.name, true)} title="Запустить только эту стадию">
                  ▶
                </button>
              </div>
            );
          })}
        </div>

        {events.length > 0 && (
          <div className="log" style={{ marginTop: 12 }}>
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

      {shorts && shorts.files.length > 0 && <ShortsView videoId={videoId} shorts={shorts} />}

      {transcript && (
        <TranscriptView transcript={transcript} onSeek={seek} currentTime={currentTime} />
      )}
    </>
  );
}
