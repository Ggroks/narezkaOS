import { useCallback, useEffect, useState } from "react";
import { api, type Health, type VideoSummary } from "./api";
import { Sidebar } from "./components/Sidebar";
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

export function App() {
  const [videoId, navigate] = useHashRoute();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Состояние панели переживает перезагрузку: ширина рабочей области —
  // личная привычка, и переустанавливать её каждый раз раздражает.
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("rail-collapsed") === "1",
  );

  const toggleRail = useCallback(() => {
    setCollapsed((was) => {
      localStorage.setItem("rail-collapsed", was ? "0" : "1");
      return !was;
    });
  }, []);

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
    <div className={`app${collapsed ? " rail-collapsed" : ""}`}>
      <a href="#content" className="sr-only">
        Перейти к содержимому
      </a>
      <Sidebar
        view={videoId ? "video" : "library"}
        videoTitle={videos.find((v) => v.video_id === videoId)?.title}
        health={health}
        collapsed={collapsed}
        onToggle={toggleRail}
        onHome={() => navigate(null)}
        count={videos.length}
      />

      <main id="content">
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
      </main>
    </div>
  );
}
