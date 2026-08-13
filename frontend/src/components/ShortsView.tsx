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
    <section className="card" aria-labelledby="shorts-heading">
      <h2 id="shorts-heading">Готовые ролики ({shorts.files.length})</h2>
      <div className="row wrap small dim" style={{ marginBottom: 12, gap: 14 }}>
        <span className="tnum">
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
              aria-label={`Ролик ${file.index + 1}`}
              src={`/api/videos/${videoId}/shorts/${file.index}/media`}
            />
            <figcaption className="small dim">
              <span>
                {formatDuration(file.start)} → {formatDuration(file.end)}
              </span>
              <span>{file.duration.toFixed(0)} с</span>
              <span>{(file.size_bytes / 1024 ** 2).toFixed(1)} МБ</span>
              <a
                href={`/api/videos/${videoId}/shorts/${file.index}/media`}
                download
                aria-label={`Скачать ролик ${file.index + 1}`}
              >
                скачать
              </a>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}
