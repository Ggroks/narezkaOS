import { useCallback, useEffect, useState } from "react";
import { api, type Health, type VideoSummary } from "./api";
import { Chrome, type StageInfo } from "./components/Chrome";
import { VideoList } from "./components/VideoList";
import { VideoDetail } from "./components/VideoDetail";

/** Маршрут в хэше: перезагрузка страницы возвращает туда же (§69). */
function useHashRoute(): [string | null, (id: string | null) => void] {
  const [videoId, setVideoId] = useState<string | null>(
    () => window.location.hash.replace(/^#\/?video\//, "") || null,
  );

  useEffect(() => {
    const onChange = () => {
      const match = window.location.hash.match(/^#\/video\/(.+)$/);
      setVideoId(match ? match[1] : null);
    };
    window.addEventListener("hashchange", onChange);
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((id: string | null) => {
    window.location.hash = id ? `/video/${id}` : "/";
  }, []);

  return [videoId, navigate];
}

/** Шесть этапов пути. Состояния пока заглушены — подключаются со стадиями. */
const PROJECT_STAGES: StageInfo[] = [
  { id: "source", title: "источник", state: "done" },
  { id: "analysis", title: "анализ", state: "done" },
  { id: "moments", title: "моменты", state: "running" },
  { id: "frame", title: "кадр", state: "waiting" },
  { id: "style", title: "стиль", state: "waiting" },
  { id: "export", title: "экспорт", state: "locked", blockedBy: "сначала выберите моменты" },
];

export function App() {
  const [videoId, navigate] = useHashRoute();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setVideos(await api.videos());
      setError(null);
    } catch (exc) {
      setError(
        exc instanceof Error && exc.message.includes("Failed to fetch")
          ? "API не отвечает. Запустите его: narezka serve"
          : String(exc),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
    api.health().then(setHealth).catch(() => setHealth(null));
  }, [refresh]);

  return (
    <Chrome
      projectName={videos.find((v) => v.video_id === videoId)?.title}
      source={health ? { duration: health.device.kind === "cuda" ? "видеокарта" : "процессор", size: health.device.name } : null}
      queue={{ waiting: 0, running: 0 }}
      filters={videoId ? undefined : <span className="rail-hint">библиотека проектов</span>}
      stages={videoId ? PROJECT_STAGES : undefined}
      activeStage={videoId ? "source" : undefined}
    >
      <a href="#content" className="sr-only">
        Перейти к содержимому
      </a>
      {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
      {videoId ? (
        <VideoDetail videoId={videoId} onBack={() => navigate(null)} />
      ) : (
        <VideoList videos={videos} onOpen={(id) => navigate(id)} onChanged={refresh} />
      )}
    </Chrome>
  );
}
