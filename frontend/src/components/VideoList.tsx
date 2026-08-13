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

  async function remove(video: VideoSummary) {
    if (!confirm(`Удалить «${video.title}» и все артефакты обработки?`)) return;
    try {
      await api.deleteVideo(video.video_id);
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <section className="card" aria-labelledby="add-heading">
        <h2 id="add-heading">Добавить видео</h2>

        <div className="row" style={{ marginBottom: 10 }}>
          <label className="grow">
            <span className="sr-only">Ссылка на YouTube или Twitch VOD</span>
            <input
              type="url"
              inputMode="url"
              placeholder="Ссылка на YouTube или Twitch VOD"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && url.trim() && add({ url: url.trim() })}
            />
          </label>
          <button className="primary" disabled={busy || !url.trim()} onClick={() => add({ url: url.trim() })}>
            Добавить
          </button>
        </div>

        <div className="row">
          <label className="grow">
            <span className="sr-only">Путь к локальному файлу</span>
            <input
              placeholder="Либо путь к локальному файлу: /home/…/stream.mp4"
              value={file}
              onChange={(e) => setFile(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && file.trim() && add({ file: file.trim() })}
            />
          </label>
          <button disabled={busy || !file.trim()} onClick={() => add({ file: file.trim() })}>
            Добавить файл
          </button>
        </div>
      </section>

      <section className="card" aria-labelledby="list-heading">
        <h2 id="list-heading">Видео ({videos.length})</h2>

        {videos.length === 0 ? (
          <p className="empty">Пока пусто. Добавьте ссылку или путь к файлу выше.</p>
        ) : (
          <ul className="video-list" style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {videos.map((video) => (
              <li key={video.video_id} className="video-item">
                {/* Ссылка, а не кнопка: открытие видео — навигация, и она
                    должна работать средней кнопкой и «в новой вкладке». */}
                <a
                  className="open grow"
                  href={`#/video/${video.video_id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    onOpen(video.video_id);
                  }}
                >
                  <span className="title">{video.title}</span>
                  <span className="small dim mono" style={{ display: "block" }}>
                    {video.video_id}
                  </span>
                </a>

                <span className="small dim tnum">{formatDuration(video.duration_seconds)}</span>
                {video.video?.width && (
                  <span className="small dim tnum">
                    {video.video.width}×{video.video.height}
                  </span>
                )}
                <span className={`badge ${video.origin === "url" ? "" : "ok"}`}>
                  {video.origin === "url" ? "ссылка" : "файл"}
                </span>
                {video.job_status === "running" && <span className="badge run">идёт обработка</span>}

                <button
                  className="icon danger"
                  aria-label={`Удалить «${video.title}»`}
                  onClick={() => remove(video)}
                >
                  <span aria-hidden="true">✕</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
