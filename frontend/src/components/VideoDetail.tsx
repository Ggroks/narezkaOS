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
  type Transcript,
  type VideoDetail as Detail,
} from "../api";
import { FramingPanel } from "./FramingPanel";
import { PerformanceView } from "./PerformanceView";
import { EpisodesPanel } from "./EpisodesPanel";
import { Hint } from "./Hint";
import { Workspace, type Tab, type TabId } from "./Workspace";
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

/** Человеческие названия стадий: внутренние имена пользователю знать неоткуда. */
const STAGE_TITLES: Record<string, string> = {
  download: "Загрузка записи",
  probe: "Проверка файла",
  extract_audio: "Извлечение звука",
  chat: "Чтение чата",
  transcribe: "Распознавание речи",
  audiotags: "Разбор звука",
  timeline: "Удаление пауз",
  candidates: "Поиск моментов",
  llm_select: "Оценка моментов",
  episodes: "Поиск эпизодов",
  facecam: "Поиск лица",
  subtitles: "Субтитры",
  metadata: "Тексты для публикации",
  render: "Сборка роликов",
  compilation: "Длинная нарезка",
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
  const tabs: Tab[] = [
    { id: "source", label: "Исходник" },
    { id: "moments", label: "Обзор моментов", count: reviewClips.length },
    { id: "framing", label: "Кадрирование" },
    {
      id: "shorts",
      label: "Короткие видео",
      count: shorts?.files.length ?? 0,
      blockedBy: shorts?.files.length ? undefined : "Появится после сборки роликов",
    },
    { id: "long", label: "Длинная нарезка" },
    { id: "results", label: "Результаты" },
  ];

  return (
    <Workspace
      tabs={tabs}
      active={tab}
      onSelect={setTab}
      title={meta.source_title || meta.source_file || videoId}
      subtitle={formatDuration(meta.duration_seconds)}
      actions={
        <>
          <button className="ghost" onClick={onBack}>
            К проектам
          </button>
          <button className="primary" disabled={running} onClick={() => start()}>
            {running ? "Обработка идёт…" : "Запустить обработку"}
          </button>
        </>
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
      </div>
      }
    >
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      {tab === "source" && (
        <>

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

        </>
      )}

      {tab === "source" && transcript && (
        <TranscriptView
          transcript={transcript}
          currentTime={currentTime}
          onSeek={seek}
        />
      )}

      {tab === "moments" && (
        <ReviewView videoId={videoId} durationSeconds={meta.duration_seconds ?? null} />
      )}

      {tab === "framing" && (
        <div className="settings-column">
          {/* Предпросмотр рядом с настройками: их правят, глядя на результат,
              а не вслепую с переходом туда-обратно. */}
          <FramingPanel videoId={videoId} busy={running} onSaved={() => void load()} />
<div className="settings-column">
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
        </div>
        </div>
      )}

      {tab === "shorts" && shorts && shorts.files.length > 0 && (
        <ShortsView videoId={videoId} shorts={shorts} />
      )}

      {tab === "long" && <EpisodesPanel videoId={videoId} />}

      {tab === "results" && (
        <>
          <PublishView videoId={videoId} />
          <PerformanceView videoId={videoId} clips={reviewClips} />
        </>
      )}
    </Workspace>
  );
}
