import { useState } from "react";
import { api, formatDuration, type VideoSummary } from "../api";

type Props = {
  videos: VideoSummary[];
  onOpen: (videoId: string) => void;
  onChanged: () => void;
};

export function VideoList({ videos, onOpen, onChanged }: Props) {
  const [url, setUrl] = useState("");
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(payload: { url?: string; file?: string }) {
    setBusy(true);
    setError(null);
    try {
      const { video_id } = await api.addVideo(payload);
      setUrl("");
      setFile("");
      onChanged();
      onOpen(video_id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {error && <div className="error">{error}</div>}

      <div className="card">
        <h2>Добавить видео</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <input
            className="grow"
            placeholder="Ссылка на YouTube или Twitch VOD"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && url.trim() && add({ url: url.trim() })}
          />
          <button className="primary" disabled={busy || !url.trim()} onClick={() => add({ url: url.trim() })}>
            Добавить
          </button>
        </div>
        <div className="row">
          <input
            className="grow"
            placeholder="Либо путь к локальному файлу: /home/…/stream.mp4"
            value={file}
            onChange={(e) => setFile(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && file.trim() && add({ file: file.trim() })}
          />
          <button disabled={busy || !file.trim()} onClick={() => add({ file: file.trim() })}>
            Добавить файл
          </button>
        </div>
      </div>

      <div className="card">
        <h2>Видео ({videos.length})</h2>
        {videos.length === 0 ? (
          <div className="empty">Пока пусто. Добавьте ссылку или файл выше.</div>
        ) : (
          <div className="video-list">
            {videos.map((video) => (
              <button key={video.video_id} className="video-item" onClick={() => onOpen(video.video_id)}>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{video.title}</div>
                  <div className="small dim mono">{video.video_id}</div>
                </div>
                <div className="small dim">{formatDuration(video.duration_seconds)}</div>
                {video.video?.width && (
                  <div className="small dim">
                    {video.video.width}×{video.video.height}
                  </div>
                )}
                <span className={`badge ${video.origin === "url" ? "" : "ok"}`}>
                  {video.origin === "url" ? "ссылка" : "файл"}
                </span>
                {video.job_status === "running" && <span className="badge run">идёт обработка</span>}
                <button
                  className="danger"
                  title="Удалить видео и все его артефакты"
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (!confirm(`Удалить ${video.title} и все артефакты?`)) return;
                    try {
                      await api.deleteVideo(video.video_id);
                      onChanged();
                    } catch (exc) {
                      setError(exc instanceof Error ? exc.message : String(exc));
                    }
                  }}
                >
                  ✕
                </button>
              </button>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
