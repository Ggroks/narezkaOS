import { useCallback, useEffect, useState } from "react";
import { api, type Performance, type PublishedClip, type ReviewClip } from "../api";

type Props = { videoId: string; clips: ReviewClip[] };

const PLATFORMS = ["youtube", "tiktok", "instagram", "vk"];

/** Поля замера. Порядок — от того, что заносят всегда, к необязательному. */
const FIELDS: { key: MetricKey; label: string; hint?: string }[] = [
  { key: "views", label: "Просмотры" },
  { key: "likes", label: "Лайки" },
  { key: "comments", label: "Комментарии" },
  { key: "shares", label: "Репосты" },
  { key: "retention", label: "Досмотр", hint: "доля, 0–1" },
  { key: "ctr", label: "CTR", hint: "доля, 0–1" },
];

type MetricKey = "views" | "likes" | "comments" | "shares" | "retention" | "ctr";

/**
 * Контур сбора данных ([§63](BAZA.md#63)).
 *
 * Отмечает клип опубликованным — в этот момент его вектор признаков
 * замораживается — и принимает фактические показатели.
 *
 * §63 требует минимума трения: если заносить метрики неудобно, их просто
 * не будут заносить, и весь контур окажется мёртвым. Поэтому форма живёт
 * на той же странице, а не на отдельном экране, и все поля необязательны:
 * заносить только просмотры — уже полезно.
 */
export function PerformanceView({ videoId, clips }: Props) {
  const [data, setData] = useState<Performance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [platform, setPlatform] = useState(PLATFORMS[0]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.performance(videoId));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [videoId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error && !data) return null;
  if (!data) return null;

  const published = new Set(data.clips.map((c) => c.clip_index));
  const publishable = clips.filter((c) => c.selected && !published.has(c.index));

  async function markPublished(index: number) {
    setBusy(true);
    setError(null);
    try {
      setData(await api.markPublished(videoId, { index, platform }));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const { report } = data;

  return (
    <section className="card" aria-labelledby="performance-heading">
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <h2 id="performance-heading" style={{ margin: 0 }}>
          Результаты публикаций
        </h2>
        <span className="small dim grow">
          опубликовано {report.published} · с метриками {report.measured}
        </span>
      </div>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <p className="small dim" style={{ maxInlineSize: "70ch", marginTop: 0 }}>
        При отметке о публикации вектор признаков клипа замораживается. Дальше стадии можно
        пересчитывать сколько угодно — запись останется той, при которой клип ушёл в публикацию,
        иначе через месяц будет непонятно, какие признаки сработали.
      </p>

      {publishable.length > 0 && (
        <div className="perf-publish">
          <label htmlFor="perf-platform" className="small dim">
            Площадка
          </label>
          <select
            id="perf-platform"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            style={{ inlineSize: "auto" }}
          >
            {PLATFORMS.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {/* Подпись отдельно, а не в каждой кнопке: «Опубликован клип 1»
              читается как утверждение о состоянии, тогда как это действие. */}
          <span className="small dim">Отметить опубликованным:</span>
          <div className="row wrap">
            {publishable.map((clip) => (
              <button
                key={clip.index}
                disabled={busy}
                onClick={() => void markPublished(clip.index)}
              >
                клип {clip.index + 1}
              </button>
            ))}
          </div>
        </div>
      )}

      {data.clips.length === 0 ? (
        <p className="empty">Опубликованных клипов пока нет.</p>
      ) : (
        <div className="perf-list">
          {data.clips.map((clip) => (
            <ClipRow key={clip.clip_id} videoId={videoId} clip={clip} onSaved={setData} />
          ))}
        </div>
      )}

      <Report report={report} />
    </section>
  );
}

function ClipRow({
  videoId,
  clip,
  onSaved,
}: {
  videoId: string;
  clip: PublishedClip;
  onSaved: (data: Performance) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function save() {
    const payload: Record<string, number> = {};
    for (const [key, raw] of Object.entries(values)) {
      const text = raw.trim().replace(",", ".");
      if (!text) continue;
      const value = Number(text);
      if (Number.isNaN(value)) {
        setProblem(`«${text}» — не число`);
        return;
      }
      payload[key] = value;
    }
    if (Object.keys(payload).length === 0) {
      setProblem("Заполните хотя бы одно поле");
      return;
    }

    setSaving(true);
    setProblem(null);
    try {
      onSaved(await api.addMetrics(videoId, { clip_id: clip.clip_id, ...payload }));
      setValues({});
    } catch (exc) {
      setProblem(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="perf-item">
      <div className="row wrap">
        <span className="grow">
          <b>{clip.title || `Клип ${clip.clip_index + 1}`}</b>
          <span className="small dim">
            {" "}
            · {clip.platform}
            {clip.interest_score != null && ` · оценка ${clip.interest_score.toFixed(2)}`}
            {clip.human_verdict && ` · человек: ${clip.human_verdict === "accept" ? "годится" : "нет"}`}
          </span>
        </span>
        {clip.views != null && (
          <span className="small tnum">
            {clip.views.toLocaleString("ru")} просмотров
            <span className="dim"> · замеров {clip.history.length}</span>
          </span>
        )}
      </div>

      <div className="perf-fields">
        {FIELDS.map((field) => (
          <label key={field.key} className="perf-field">
            <span className="small dim">
              {field.label}
              {field.hint && <span className="perf-hint"> {field.hint}</span>}
            </span>
            <input
              type="text"
              inputMode="decimal"
              value={values[field.key] ?? ""}
              onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
              placeholder="—"
            />
          </label>
        ))}
      </div>

      <div className="row wrap">
        <button className="primary" disabled={saving} onClick={() => void save()}>
          {saving ? "Сохранение…" : "Записать замер"}
        </button>
        {problem && (
          <span className="small" style={{ color: "var(--bad)" }} role="alert">
            {problem}
          </span>
        )}
        {clip.measured_at && (
          <span className="small dim">последний замер {clip.measured_at.slice(0, 10)}</span>
        )}
      </div>
    </article>
  );
}

function Report({ report }: { report: Performance["report"] }) {
  const { correlations, verdicts, bounds } = report;
  const entries = Object.entries(correlations.factors).sort(
    (a, b) => Math.abs(b[1].correlation ?? 0) - Math.abs(a[1].correlation ?? 0),
  );

  const nothing = entries.length === 0 && verdicts.gap == null && bounds.mean_start_shift == null;
  if (nothing) return null;

  return (
    <div className="perf-report">
      <h3 className="small dim">Что показывают накопленные данные</h3>

      {verdicts.gap != null && (
        <p className="small">
          Оценка модели у принятых клипов <b className="tnum">{verdicts.mean_accepted}</b>, у
          отклонённых <b className="tnum">{verdicts.mean_rejected}</b> — разрыв{" "}
          <b className="tnum">{verdicts.gap > 0 ? "+" : ""}{verdicts.gap}</b>.
          {!verdicts.reliable && <span className="dim"> Наблюдений мало.</span>}
        </p>
      )}

      {bounds.mean_start_shift != null && (
        <p className="small">
          Границы вы двигаете в среднем на <b className="tnum">{bounds.mean_start_shift}</b> с в
          начале и <b className="tnum">{bounds.mean_end_shift}</b> с в конце.
          {!bounds.reliable && <span className="dim"> Наблюдений мало.</span>}
        </p>
      )}

      {entries.length > 0 && (
        <>
          <table className="perf-table">
            <caption className="small dim">
              Связь признаков с метрикой «{correlations.metric}»
            </caption>
            <thead>
              <tr>
                <th scope="col">Признак</th>
                <th scope="col">Связь</th>
                <th scope="col">Наблюдений</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(([name, entry]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="tnum">
                    {entry.correlation == null
                      ? "—"
                      : `${entry.correlation > 0 ? "+" : ""}${entry.correlation}`}
                  </td>
                  <td className="tnum">{entry.sample}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {/* Прямое предупреждение, а не мелкая сноска: на малой выборке
              коэффициент скачет от добавления одной строки. */}
          {!correlations.reliable && (
            <p className="small" style={{ color: "var(--warn)" }}>
              Наблюдений {correlations.sample}, надёжно от {correlations.min_sample}. Принимать
              решения по этим числам пока рано.
            </p>
          )}
        </>
      )}
    </div>
  );
}
