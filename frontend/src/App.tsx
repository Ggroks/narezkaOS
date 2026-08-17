import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Health, type VideoSummary } from "./api";
import { ThemePicker } from "./components/ThemePicker";
import { ProjectCatalog } from "./components/ProjectCatalog";
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

/** Как часто обновлять каталог, пока хоть что-то обрабатывается. */
const POLL_MS = 3000;

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

  /**
   * Живой ход работы в каталоге — опросом, а не потоком событий.
   *
   * Поток (`subscribeToJob`) подписан на одну запись, и для каталога
   * пришлось бы держать по соединению на карточку. Событий здесь немного,
   * а обновление раз в три секунды человек воспринимает как живое. Опрос
   * идёт, только пока что-то обрабатывается, и только когда каталог открыт:
   * висеть в фоне без дела ему незачем.
   */
  const busy = videos.some((video) => video.state === "processing");
  // Ссылка на свежую функцию: интервал ставится один раз на «занят/не занят»,
  // а не пересоздаётся при каждом обновлении списка.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (videoId !== null || !busy) return;
    const timer = setInterval(() => void refreshRef.current(), POLL_MS);
    return () => clearInterval(timer);
  }, [busy, videoId]);

  // Возврат к вкладке — повод обновиться сразу: за время отсутствия
  // обработка могла закончиться, и ждать очередного тика незачем.
  useEffect(() => {
    if (videoId !== null) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshRef.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [videoId]);

  return (
    <div className="app">
      <a href="#content" className="sr-only">
        Перейти к содержимому
      </a>
      <header className="top">
        <h1>Narezka OS</h1>
        <ThemePicker />
        {health && (
          <span className="env small dim">
            профиль {health.profile} · {health.device.kind} · {health.device.name}
          </span>
        )}
      </header>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <main id="content">
        {videoId ? (
          <VideoDetail videoId={videoId} onBack={() => navigate(null)} />
        ) : (
          <ProjectCatalog projects={videos} onOpen={(id) => navigate(id)} onChanged={refresh} />
        )}
      </main>
    </div>
  );
}
