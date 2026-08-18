import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { PreviewSlot } from "./PreviewSlot";
import { api, type Framing, type FramingState } from "../api";

type Props = {
  videoId: string;
  /** Рендер идёт — менять кадрирование в этот момент бессмысленно. */
  busy: boolean;
  /** Настройки сохранены — обновить остальной экран. Сборку запускает
      кнопка вкладки, а не эта панель. */
  onSaved: () => void;
  /** Занимать ли полосу предпросмотра: она одна на экран. */
  preview?: boolean;
};

const ANCHORS: { value: Framing["anchor"]; label: string }[] = [
  { value: "left", label: "Слева" },
  { value: "center", label: "По центру" },
  { value: "right", label: "Справа" },
];

/** Задержка перед запросом кадра: пока тянут ползунок, запросы не нужны. */
const PREVIEW_DELAY_MS = 350;

const percent = (value: number) => `${Math.round(value * 100)}%`;

/**
 * Доля высоты кадра, которую займёт содержимое при заданной обрезке.
 *
 * Повторяет расчёт сервера, чтобы подпись у ползунка «Вручную» менялась
 * сразу, а не после ответа. Округление до чётных сторон здесь опущено —
 * на долю оно влияет меньше чем на 0.2%, а истина всё равно на сервере.
 */
function contentShare(
  source: { width: number; height: number },
  output: { width: number; height: number },
  sideCrop: number,
): number {
  const keptWidth = source.width * (1 - sideCrop);
  if (keptWidth <= 0) return 1;
  return Math.min((output.width * source.height) / keptWidth / output.height, 1);
}

type OptionProps = {
  label: string;
  checked: boolean;
  disabled: boolean;
  contentShare: number;
  sideCrop: number;
  onChange: () => void;
};

/**
 * Строка выбора обрезки.
 *
 * Миниатюра слева — тот же кадр 9:16, закрашенная полоса в ней равна доле
 * содержимого. Компромисс между «видно больше» и «теряем края» виден
 * раньше, чем прочитаны числа.
 */
function Option({ label, checked, disabled, contentShare, sideCrop, onChange }: OptionProps) {
  return (
    <label className="framing-option">
      <input
        type="radio"
        name="framing-preset"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
      <span
        className="framing-glyph"
        aria-hidden="true"
        style={{ "--share": percent(contentShare) } as CSSProperties}
      />
      <span className="name">{label}</span>
      <span className="figure tnum">
        <span className="share">{percent(contentShare)}</span>
        <span className="crop">срез {percent(sideCrop)}</span>
      </span>
    </label>
  );
}

/**
 * Настройка вертикального кадра ([§61](BAZA.md#61)).
 *
 * Главное, что должен показывать этот экран, — связь между обрезкой по бокам
 * и высотой содержимого: это один параметр, а не два. Поэтому у каждого
 * варианта подписаны оба числа, а рядом всегда висит настоящий кадр из этого
 * же видео: настраивать рамку по описанию словами невозможно.
 */
export function FramingPanel({ videoId, busy, onSaved, preview = true }: Props) {
  const [state, setState] = useState<FramingState | null>(null);
  const [draft, setDraft] = useState<Framing | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .framing(videoId)
      .then((data) => {
        if (!alive) return;
        setState(data);
        setDraft(data.current);
      })
      .catch((exc) => alive && setError(exc instanceof Error ? exc.message : String(exc)));
    return () => {
      alive = false;
    };
  }, [videoId]);

  // Предпросмотр обновляется с задержкой — иначе каждый шаг ползунка
  // запускал бы отдельный вызов ffmpeg.
  useEffect(() => {
    if (!draft) return;
    const timer = window.setTimeout(() => {
      setPreviewLoading(true);
      setPreviewUrl(api.framingPreviewUrl(videoId, draft));
    }, PREVIEW_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [videoId, draft]);

  const chosen = useMemo(() => {
    if (!state || !draft) return null;
    if (draft.preset === "custom") return null;
    return state.presets.find((p) => p.preset === draft.preset) ?? null;
  }, [state, draft]);

  if (error && !state) {
    return (
      <section className="card">
        <h2>Кадрирование</h2>
        <div className="error" role="alert">
          {error}
        </div>
      </section>
    );
  }

  if (!state || !draft) {
    return (
      <section className="card">
        <h2>Кадрирование</h2>
        <p className="empty">Загрузка…</p>
      </section>
    );
  }

  const update = (patch: Partial<Framing>) => setDraft({ ...draft, ...patch });

  // При custom доля обрезки задаётся вручную, иначе берётся у пресета.
  const sideCrop = draft.preset === "custom" ? draft.side_crop : (chosen?.side_crop ?? 0);
  const fullBleed = draft.preset === "custom" ? false : (chosen?.full_bleed ?? false);
  const customShare = contentShare(state.source, state.output, draft.side_crop);
  const changed = JSON.stringify(draft) !== JSON.stringify(state.current);

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      setState(await api.setFraming(videoId, draft));
      onSaved();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setSaving(true);
    setError(null);
    try {
      const data = await api.resetFraming(videoId);
      setState(data);
      setDraft(data.current);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="card" aria-labelledby="framing-heading">
      <h2 id="framing-heading">Кадрирование</h2>
      <p className="small dim framing-intro">
        Исходник {state.source.width}×{state.source.height} вписывается в{" "}
        {state.output.width}×{state.output.height} по ширине. Чем больше отрезано по бокам,
        тем крупнее содержимое и тем уже полосы — это один параметр, а не два.
      </p>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <div className="framing">
        <PreviewSlot active={preview}>
          <div className="framing-preview">
            {previewUrl && (
              <img
                src={previewUrl}
                alt="Предпросмотр кадра с текущими настройками"
                onLoad={() => setPreviewLoading(false)}
                onError={() => setPreviewLoading(false)}
              />
            )}
            {previewLoading && (
              <span className="preview-note small dim" role="status">
                обновляется…
              </span>
            )}
          </div>
        </PreviewSlot>

        <div className="framing-controls">
          <fieldset>
            <legend>Сколько видно</legend>
            <div className="framing-presets">
              {state.presets.map((preset) => (
                <Option
                  key={preset.preset}
                  label={preset.label}
                  checked={draft.preset === preset.preset}
                  disabled={busy}
                  contentShare={preset.content_share}
                  sideCrop={preset.side_crop}
                  onChange={() => update({ preset: preset.preset })}
                />
              ))}

              <Option
                label="Вручную"
                checked={draft.preset === "custom"}
                disabled={busy}
                contentShare={customShare}
                sideCrop={draft.side_crop}
                onChange={() => update({ preset: "custom", side_crop: sideCrop })}
              />
            </div>

            {draft.preset === "custom" && (
              <div className="framing-slider">
                <label htmlFor="side-crop">Обрезка по бокам</label>
                <input
                  id="side-crop"
                  type="range"
                  min={0}
                  max={95}
                  step={1}
                  value={Math.round(draft.side_crop * 100)}
                  disabled={busy}
                  onChange={(e) => update({ side_crop: Number(e.target.value) / 100 })}
                />
                <output htmlFor="side-crop" className="tnum">
                  {percent(draft.side_crop)}
                </output>
              </div>
            )}
          </fieldset>

          <fieldset>
            <legend>Какую часть кадра оставить</legend>
            <div className="framing-row" role="radiogroup" aria-label="Какую часть кадра оставить">
              {ANCHORS.map((anchor) => (
                <label key={anchor.value} className="framing-chip">
                  <input
                    type="radio"
                    name="framing-anchor"
                    value={anchor.value}
                    checked={draft.anchor === anchor.value}
                    disabled={busy || sideCrop === 0}
                    onChange={() => update({ anchor: anchor.value })}
                  />
                  <span>{anchor.label}</span>
                </label>
              ))}
            </div>
            {sideCrop === 0 && (
              <p className="small dim">Ничего не отрезается — выбирать нечего.</p>
            )}
          </fieldset>

          <fieldset disabled={fullBleed}>
            <legend>Полосы сверху и снизу</legend>
            {fullBleed ? (
              <p className="small dim">
                Содержимое заполняет кадр целиком — полос нет, подложка не видна.
              </p>
            ) : (
              <>
                <div className="framing-row" role="radiogroup" aria-label="Чем заполнить полосы">
                  <label className="framing-chip">
                    <input
                      type="radio"
                      name="framing-background"
                      value="blur"
                      checked={draft.background === "blur"}
                      disabled={busy}
                      onChange={() => update({ background: "blur" })}
                    />
                    <span>Кадр из видео</span>
                  </label>
                  <label className="framing-chip">
                    <input
                      type="radio"
                      name="framing-background"
                      value="color"
                      checked={draft.background === "color"}
                      disabled={busy}
                      onChange={() => update({ background: "color" })}
                    />
                    <span>Однотонная</span>
                  </label>
                </div>

                {draft.background === "blur" ? (
                  <div className="framing-slider">
                    <label htmlFor="blur-sigma">Размытие</label>
                    <input
                      id="blur-sigma"
                      type="range"
                      min={0}
                      max={80}
                      step={1}
                      value={draft.blur_sigma}
                      disabled={busy}
                      onChange={(e) => update({ blur_sigma: Number(e.target.value) })}
                    />
                    <output htmlFor="blur-sigma" className="tnum">
                      {draft.blur_sigma === 0 ? "выключено" : draft.blur_sigma}
                    </output>
                  </div>
                ) : (
                  <div className="framing-slider">
                    <label htmlFor="bg-color">Цвет</label>
                    <input
                      id="bg-color"
                      type="color"
                      value={`#${draft.color.replace(/^0x|^#/, "")}`}
                      disabled={busy}
                      onChange={(e) => update({ color: `0x${e.target.value.slice(1)}` })}
                    />
                    <output htmlFor="bg-color" className="mono small dim">
                      {draft.color}
                    </output>
                  </div>
                )}
              </>
            )}
          </fieldset>

          <div className="framing-actions">
            {/* Кнопка только сохраняет. Сборкой ведает одна кнопка на всю
                вкладку: две кнопки, запускающие рендер с разных мест экрана,
                — это способ запустить его дважды. */}
            <button className="primary" disabled={busy || saving || !changed} onClick={save}>
              {saving ? "Сохранение…" : "Запомнить кадр"}
            </button>
            <button disabled={busy || saving || !state.custom} onClick={reset}>
              Вернуть как было
            </button>
            <span className="small dim status">
              {changed
                ? "Не сохранено — ролики пока со старой рамкой."
                : `${state.plan.summary} · применится при сборке`}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
