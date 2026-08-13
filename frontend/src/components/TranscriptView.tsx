import { useId, useMemo, useState } from "react";
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
  const toggleId = useId();

  const segments = useMemo(
    () => (showSuspect ? transcript.segments : transcript.segments.filter((s) => !s.suspect)),
    [transcript.segments, showSuspect],
  );

  const { segments_total, segments_suspect, words_usable } = transcript.stats;

  return (
    <section className="card" aria-labelledby="transcript-heading">
      <h2 id="transcript-heading">Транскрипт</h2>

      <div className="row wrap small dim" style={{ marginBottom: 12, gap: 14 }}>
        <span>
          язык <b style={{ color: "var(--text)" }}>{transcript.language}</b>{" "}
          <span className="tnum">({Math.round(transcript.language_probability * 100)}%)</span>
        </span>
        <span>модель {transcript.model}</span>
        <span className="tnum">сегментов {segments_total}</span>
        {segments_suspect > 0 && (
          <span className="badge warn tnum">подозрительных {segments_suspect}</span>
        )}
        <span className="tnum">слов {words_usable}</span>

        <label
          htmlFor={toggleId}
          className="row small"
          style={{ marginInlineStart: "auto", gap: 6, cursor: "pointer", width: "auto" }}
        >
          <input
            id={toggleId}
            type="checkbox"
            checked={showSuspect}
            onChange={(e) => setShowSuspect(e.target.checked)}
            style={{ width: "auto", flex: "none" }}
          />
          показывать подозрительные
        </label>
      </div>

      {segments.length === 0 ? (
        <p className="empty">
          {segments_total === 0
            ? "Речь не найдена. Для записи без голоса это правильно — определение речи отсекло не-речь до распознавания."
            : "Все сегменты отфильтрованы."}
        </p>
      ) : (
        <div className="segments">
          {segments.map((segment) => {
            const current = currentTime >= segment.start && currentTime < segment.end;
            return (
              <button
                key={segment.id}
                type="button"
                className={`segment${segment.suspect ? " suspect" : ""}${current ? " current" : ""}`}
                onClick={() => onSeek(segment.start)}
                aria-current={current ? "true" : undefined}
              >
                <span className="time">{formatDuration(segment.start)}</span>
                <span>
                  <span className="text">{segment.text}</span>
                  {segment.suspect && (
                    <span className="reason" style={{ display: "block" }}>
                      {/* Значок дублируется словом: смысл не должен держаться
                          на одном цвете или символе. */}
                      <span aria-hidden="true">⚠ </span>
                      Подозрительно: {segment.suspect_reason}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
