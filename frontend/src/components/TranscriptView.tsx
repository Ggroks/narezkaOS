import { useMemo, useState } from "react";
import { formatDuration, type Transcript } from "../api";

type Props = {
  transcript: Transcript;
  onSeek: (seconds: number) => void;
  currentTime: number;
};

/**
 * Просмотр транскрипта.
 *
 * Подозрительные сегменты (§57) показываются с причиной, а не прячутся:
 * по ним видно, что именно фильтр считает выдумкой модели, — иначе настроить
 * пороги невозможно.
 */
export function TranscriptView({ transcript, onSeek, currentTime }: Props) {
  const [showSuspect, setShowSuspect] = useState(true);

  const segments = useMemo(
    () => (showSuspect ? transcript.segments : transcript.segments.filter((s) => !s.suspect)),
    [transcript.segments, showSuspect],
  );

  const { segments_total, segments_suspect, words_usable } = transcript.stats;

  return (
    <div className="card">
      <h2>Транскрипт</h2>

      <div className="row wrap small dim" style={{ marginBottom: 12, gap: 14 }}>
        <span>
          язык <b style={{ color: "var(--text)" }}>{transcript.language}</b>{" "}
          ({Math.round(transcript.language_probability * 100)}%)
        </span>
        <span>модель {transcript.model}</span>
        <span>сегментов {segments_total}</span>
        {segments_suspect > 0 && (
          <span className="badge warn">подозрительных {segments_suspect}</span>
        )}
        <span>слов {words_usable}</span>
        <label className="row small" style={{ marginLeft: "auto", gap: 6, cursor: "pointer", width: "auto" }}>
          <input
            type="checkbox"
            checked={showSuspect}
            onChange={(e) => setShowSuspect(e.target.checked)}
            style={{ width: "auto" }}
          />
          показывать подозрительные
        </label>
      </div>

      {segments.length === 0 ? (
        <div className="empty">
          {segments_total === 0
            ? "Речь не найдена. Для синтетического звука это правильно — VAD отсёк не-речь."
            : "Все сегменты отфильтрованы."}
        </div>
      ) : (
        <div className="segments">
          {segments.map((segment) => {
            const active = currentTime >= segment.start && currentTime < segment.end;
            return (
              <div
                key={segment.id}
                className={`segment${segment.suspect ? " suspect" : ""}`}
                style={active ? { background: "var(--surface-2)", borderColor: "var(--accent)" } : undefined}
                onClick={() => onSeek(segment.start)}
                title="Перейти к этому месту"
              >
                <div className="time">{formatDuration(segment.start)}</div>
                <div>
                  <div>{segment.text}</div>
                  {segment.suspect && <div className="reason">⚠ {segment.suspect_reason}</div>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
