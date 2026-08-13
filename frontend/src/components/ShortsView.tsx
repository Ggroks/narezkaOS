import { formatDuration, type ShortsIndex } from "../api";

type Props = { videoId: string; shorts: ShortsIndex };

/**
 * Готовые вертикальные ролики.
 *
 * Это единственный экран, где видно конечный продукт, поэтому ролики
 * показываются в реальной пропорции 9:16 — так же, как их увидит зритель,
 * включая то, не залезли ли субтитры в зону интерфейса платформы (§60).
 */
export function ShortsView({ videoId, shorts }: Props) {
  return (
    <div className="card">
      <h2>Готовые ролики ({shorts.files.length})</h2>
      <div className="row wrap small dim" style={{ marginBottom: 12, gap: 14 }}>
        <span>
          {shorts.width}×{shorts.height}
        </span>
        <span>фон: {shorts.background === "blur" ? "размытый кадр" : "однотонный"}</span>
      </div>

      <div className="shorts-grid">
        {shorts.files.map((file) => (
          <figure key={file.index} className="short">
            <video
              controls
              preload="metadata"
              src={`/api/videos/${videoId}/shorts/${file.index}/media`}
            />
            <figcaption className="small dim">
              <span>
                {formatDuration(file.start)} → {formatDuration(file.end)}
              </span>
              <span>{file.duration.toFixed(0)} с</span>
              <span>{(file.size_bytes / 1024 ** 2).toFixed(1)} МБ</span>
              <a href={`/api/videos/${videoId}/shorts/${file.index}/media`} download>
                скачать
              </a>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
