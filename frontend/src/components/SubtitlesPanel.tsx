import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type SubtitleOptions,
  type SubtitlesState,
} from "../api";
import { Hint } from "./Hint";

/**
 * Оформление субтитров: готовые наборы и тонкая настройка поверх.
 *
 * **Почему с кадром.** Настраивать шрифт и цвет по описанию словами
 * невозможно, а полный рендер ради проверки занимает минуты. Рядом всегда
 * настоящий кадр записи с вшитыми субтитрами — тот же приём, что
 * у предпросмотра рамки, и та же цепочка фильтров, что у сборки: собери
 * предпросмотр отдельно, он однажды разойдётся с результатом.
 *
 * **Почему наборы названы словами.** «STYLE_2» не говорит ничего, пока
 * не увидишь. Названия описывают то, что видно в кадре, а подпись рядом —
 * кому это подходит.
 */

type Props = { videoId: string; disabled?: boolean };

/**
 * Пауза перед отправкой правки. Ползунок за одно движение выдаёт десятки
 * изменений; без паузы каждое уходило запросом, ответы возвращались
 * вперемешку, и итоговое значение оказывалось не тем, что выставил человек:
 * замер показал 2 вместо 4.
 */
const SAVE_DELAY_MS = 350;

export function SubtitlesPanel({ videoId, disabled }: Props) {
  const [options, setOptions] = useState<SubtitleOptions | null>(null);
  const [state, setState] = useState<SubtitlesState | null>(null);
  const [version, setVersion] = useState("0");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  //: Отложенная отправка: ползунок двигают, а не щёлкают.
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    api.subtitleOptions().then((data) => alive && setOptions(data)).catch(() => setOptions(null));
    api
      .subtitles(videoId)
      .then((data) => alive && setState(data))
      .catch(() => setState(null));
    return () => {
      alive = false;
    };
  }, [videoId]);

  /**
   * Правка: сразу в состояние на экране, на сервер — с паузой.
   *
   * Экран отзывается мгновенно, а запросов уходит один на движение ползунка,
   * а не десятки. Отправляется весь набор полей целиком: сервер хранит
   * правки как есть, и слать только изменившееся значило бы уповать на то,
   * что остальные он помнит верно.
   */
  const save = useCallback(
    (patch: Record<string, unknown>) => {
      setState((current) => {
        if (!current) return current;
        const style = { ...current.style, ...patchToStyle(patch) };
        const next = { ...current, style, custom: true };

        if (timer.current) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          setLoading(true);
          api
            .setSubtitles(videoId, {
              preset: next.preset,
              font: style.font,
              font_size: style.font_size,
              primary: style.primary_hex,
              highlight: style.highlight_hex,
              outline_colour: style.outline_hex,
              outline: style.outline,
              bold: style.bold,
              position: style.position,
              max_words_per_line: style.max_words_per_line,
              max_chars_per_line: style.max_chars_per_line,
              max_lines: style.max_lines,
            })
            .then((saved) => {
              setState(saved);
              setVersion(String(Date.now()));
              setError(null);
            })
            .catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
        }, SAVE_DELAY_MS);

        return next;
      });
    },
    [videoId],
  );

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  /** Смена набора не тащит за собой прежние правки: человек выбирает
   *  готовое оформление, а не готовое поверх своего. */
  async function choose(preset: string) {
    if (timer.current) window.clearTimeout(timer.current);
    setLoading(true);
    try {
      setState(await api.setSubtitles(videoId, { preset }));
      setVersion(String(Date.now()));
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  if (!state || !options) return null;
  const style = state.style;

  return (
    <section className="card subtitles-panel" aria-labelledby="subs-heading">
      <div className="row wrap" style={{ marginBottom: 12 }}>
        <h2 id="subs-heading" style={{ margin: 0 }}>
          Субтитры
        </h2>
        {state.custom && <span className="badge">свои настройки</span>}
        <span className="grow" />
        {state.custom && (
          <button
            className="ghost"
            disabled={disabled}
            onClick={() => void choose(state.preset)}
          >
            Вернуть набор
          </button>
        )}
      </div>

      <div className="subs-layout">
        <figure className="subs-preview">
          {/* Кадр перерисовывается после каждой правки: адрес тот же,
              поэтому в нём меняется метка версии — иначе браузер показал
              бы прежний из кэша. */}
          <img
            src={api.subtitlePreviewUrl(videoId, version)}
            alt="Кадр записи с субтитрами"
            onLoad={() => setLoading(false)}
            onError={() => setLoading(false)}
          />
          {loading && <figcaption className="small dim">Готовим кадр…</figcaption>}
        </figure>

        <div className="subs-controls">
          <h4 className="group-title">Готовые наборы</h4>
          <div className="subs-presets">
            {options.presets.map((preset) => (
              <label
                key={preset.name}
                className={`subs-preset${state.preset === preset.name ? " current" : ""}`}
              >
                <input
                  type="radio"
                  name="subs-preset"
                  className="sr-only"
                  checked={state.preset === preset.name}
                  disabled={disabled}
                  onChange={() => void choose(preset.name)}
                />
                <span
                  className="subs-swatch"
                  aria-hidden="true"
                  style={{ color: preset.colours.highlight }}
                >
                  Аа
                </span>
                <span className="subs-preset-text">
                  <b>{preset.title}</b>
                  <span className="small dim">{preset.note}</span>
                </span>
              </label>
            ))}
          </div>

          <h4 className="group-title">Где и сколько</h4>
          <label className="choice">
            <span className="choice-label">
              Положение в кадре
              <Hint>
                Снизу — привычно и не спорит с лицом. По центру — когда внизу
                происходит главное. Сверху — если площадка закрывает низ
                своими подписями.
              </Hint>
            </span>
            <div className="segmented">
              {options.positions.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  className={style.position === item.name ? "current" : ""}
                  disabled={disabled}
                  onClick={() => void save({ position: item.name })}
                >
                  {item.title}
                </button>
              ))}
            </div>
          </label>

          <label className="slider">
            <span className="small dim">
              Слов в строке: <b className="tnum">{style.max_words_per_line}</b>
            </span>
            <input
              type="range"
              min={1}
              max={8}
              step={1}
              value={style.max_words_per_line}
              disabled={disabled}
              onChange={(e) => void save({ max_words_per_line: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Одно слово удерживает внимание, но читается тяжелее. Длинное слово
            всё равно перенесётся — ширину держит второй предел, по символам.
          </span>

          <label className="slider">
            <span className="small dim">
              Строк на экране: <b className="tnum">{style.max_lines}</b>
            </span>
            <input
              type="range"
              min={1}
              max={4}
              step={1}
              value={style.max_lines}
              disabled={disabled}
              onChange={(e) => void save({ max_lines: Number(e.target.value) })}
            />
          </label>

          <h4 className="group-title">Как выглядит</h4>
          <label className="choice">
            <span className="choice-label">
              Шрифт
              <Hint>
                Шрифты лежат в поставке, а не берутся с машины: иначе на другом
                компьютере ролик вышел бы другим, а кириллица могла бы стать
                пустыми прямоугольниками.
              </Hint>
            </span>
            <select
              value={style.font}
              disabled={disabled}
              onChange={(e) => void save({ font: e.target.value })}
            >
              {options.fonts.map((font) => (
                <option key={font.name} value={font.name}>
                  {font.name}
                  {font.note ? ` — ${font.note}` : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="slider">
            <span className="small dim">
              Размер: <b className="tnum">{style.font_size}</b>
            </span>
            <input
              type="range"
              min={32}
              max={120}
              step={2}
              value={style.font_size}
              disabled={disabled}
              onChange={(e) => void save({ font_size: Number(e.target.value) })}
            />
          </label>

          <div className="subs-colours">
            <label>
              <span className="small dim">Текст</span>
              <input
                type="color"
                value={style.primary_hex}
                disabled={disabled}
                onChange={(e) => void save({ primary: e.target.value })}
              />
            </label>
            <label>
              <span className="small dim">Текущее слово</span>
              <input
                type="color"
                value={style.highlight_hex}
                disabled={disabled}
                onChange={(e) => void save({ highlight: e.target.value })}
              />
            </label>
            <label>
              <span className="small dim">Обводка</span>
              <input
                type="color"
                value={style.outline_hex}
                disabled={disabled}
                onChange={(e) => void save({ outline_colour: e.target.value })}
              />
            </label>
          </div>

          <label className="slider">
            <span className="small dim">
              Толщина обводки: <b className="tnum">{style.outline.toFixed(1)}</b>
            </span>
            <input
              type="range"
              min={0}
              max={8}
              step={0.5}
              value={style.outline}
              disabled={disabled}
              onChange={(e) => void save({ outline: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Обводка держит текст читаемым на светлом кадре. Ноль — только если
            уверены, что фон под субтитрами всегда тёмный.
          </span>
        </div>
      </div>

      {error && (
        <div className="error" role="alert" style={{ marginTop: 12, marginBottom: 0 }}>
          {error}
        </div>
      )}
    </section>
  );
}


/**
 * Правка из интерфейса в поля стиля.
 *
 * Цвета в состоянии лежат под своими именами (`highlight_hex`), а на сервер
 * уходят под общими (`highlight`). Перевод здесь, в одном месте: иначе
 * каждое поле цвета помнило бы про два имени.
 */
function patchToStyle(patch: Record<string, unknown>): Record<string, unknown> {
  const names: Record<string, string> = {
    primary: "primary_hex",
    highlight: "highlight_hex",
    outline_colour: "outline_hex",
  };
  return Object.fromEntries(
    Object.entries(patch).map(([key, value]) => [names[key] ?? key, value]),
  );
}
