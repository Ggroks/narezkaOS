import type { DetectorsInfo, EncodersInfo, Framing, ModelsInfo } from "../api";

/**
 * Настройки, разложенные по тому, на что они влияют.
 *
 * Раньше все два десятка стояли одним списком на вкладке кадрирования, и
 * понять, что именно изменится от переключателя, было нельзя: сигналы поиска
 * моментов стояли рядом с размытием фона. Теперь настройка живёт там, где
 * стоит кнопка, которая её применит:
 *
 * - `AnalysisSettings` — вкладка «Исходник»: по чему искать моменты;
 * - `ShortSettings` — вкладка «Короткие видео»: что попадёт в готовый ролик.
 *
 * Выбор для длинной нарезки живёт в `EpisodesPanel` по той же причине.
 *
 * Каждая возможность отключается отдельно и по умолчанию включена: нарезка
 * должна оставаться полезной и в неполном составе — кому-то нужен ролик без
 * субтитров под свой монтаж, кому-то без нормализации, потому что звук
 * уже сведён.
 */

type Common = {
  value: Framing;
  onChange: (patch: Partial<Framing>) => void;
  disabled?: boolean;
};

export function AnalysisSettings({
  value, onChange, disabled, models,
}: Common & { models?: ModelsInfo | null }) {
  return (
    <div className="options">
      {/* Материал бывает разный: на музыкальном стриме громкость ровная и
          ничего не различает, на записи без чата чат бесполезен. Выключенный
          сигнал исключается из расчёта, а не обнуляется — иначе он тянул бы
          оценку вниз как измеренный ноль. */}
      <h4 className="group-title">Где искать интересное</h4>
      <div className="choice">
        <Toggle
          label="Громкость"
          hint="всплеск звука; на музыке почти не различает"
          checked={value.use_loudness}
          disabled={disabled}
          onChange={(v) => onChange({ use_loudness: v })}
        />
        <Toggle
          label="Плотность речи"
          hint="слов в секунду: спор и объяснение звучат по-разному"
          checked={value.use_speech_rate}
          disabled={disabled}
          onChange={(v) => onChange({ use_speech_rate: v })}
        />
        <Toggle
          label="Активность чата"
          hint="сколько пишут; нужна запись с Twitch"
          checked={value.use_chat}
          disabled={disabled}
          onChange={(v) => onChange({ use_chat: v })}
        />
        <Toggle
          label="Реакции в чате"
          hint="смайлы, повторы, междометия — пишут ли в ответ на происходящее"
          checked={value.use_chat_reactions}
          disabled={disabled}
          onChange={(v) => onChange({ use_chat_reactions: v })}
        />
        <Toggle
          label="Пропускать приветствия"
          hint="в начале записи здороваются, а не реагируют на происходящее"
          checked={value.chat_ignore_start}
          disabled={disabled}
          onChange={(chat_ignore_start) => onChange({ chat_ignore_start })}
        />
      </div>

      {/* Смех полезен почти всем, музыка — тем, кто публикует ролики и рискует
          правами на неё, аплодисменты и толпа осмысленны лишь на записях
          с залом. Считать то, чем не пользуются, значит платить временем
          за ничто. */}
      <h4 className="group-title">Что слышать в звуке</h4>
      <div className="choice">
        <Toggle
          label="Смех"
          hint="повышает оценку момента; сильнейший признак удачного"
          checked={value.tag_laughter}
          disabled={disabled}
          onChange={(v) => onChange({ tag_laughter: v })}
        />
        <Toggle
          label="Музыка"
          hint="снижает оценку: за чужую музыку площадка может закрыть доступ к ролику или забрать доход"
          checked={value.tag_music}
          disabled={disabled}
          onChange={(v) => onChange({ tag_music: v })}
        />
        <Toggle
          label="Крик"
          hint="и восторг, и испуг — сам по себе неоднозначен"
          checked={value.tag_shout}
          disabled={disabled}
          onChange={(v) => onChange({ tag_shout: v })}
        />
        <Toggle
          label="Аплодисменты"
          hint="имеет смысл только на записях с залом"
          checked={value.tag_applause}
          disabled={disabled}
          onChange={(v) => onChange({ tag_applause: v })}
        />
        <Toggle
          label="Шум толпы"
          hint="отличает публичное событие от студии"
          checked={value.tag_crowd}
          disabled={disabled}
          onChange={(v) => onChange({ tag_crowd: v })}
        />
      </div>

      {models && models.models.length > 0 && (
        <>
          <h4 className="group-title">Кто оценивает моменты</h4>
          <label className="choice">
            <span className="sr-only">Модель отбора</span>
            <select
              value={value.llm_model ?? models.selected ?? ""}
              disabled={disabled}
              onChange={(e) => onChange({ llm_model: e.target.value })}
            >
              {models.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id.split("/").pop()}
                  {m.free ? " · бесплатно" : ""}
                </option>
              ))}
            </select>
            <span className="choice-hint">
              {models.provider} · читает расшифровку и решает, какие места
              стоят ролика
            </span>
          </label>
        </>
      )}
    </div>
  );
}

export function ShortSettings({
  value, onChange, splitAvailable, disabled, detectors, encoders, faceZoom,
}: Common & {
  /** Раскладка «сплит» доступна только там, где найдена вебка наложением. */
  splitAvailable: boolean;
  detectors?: DetectorsInfo | null;
  encoders?: EncodersInfo | null;
  /** Готовые степени приближения лица. */
  faceZoom?: { name: string; title: string; note: string; zoom: number }[];
}) {
  return (
    <div className="options">
      <h4 className="group-title">Что войдёт в ролик</h4>
      <Toggle
        label="Субтитры"
        hint="Вшиваются в кадр, с подсветкой слова"
        checked={value.subtitles_enabled}
        disabled={disabled}
        onChange={(subtitles_enabled) => onChange({ subtitles_enabled })}
      />
      <Toggle
        label="Ровная громкость"
        hint="Приводит все ролики к одному уровню"
        checked={value.loudnorm_enabled}
        disabled={disabled}
        onChange={(loudnorm_enabled) => onChange({ loudnorm_enabled })}
      />

      <h4 className="group-title">Как показать кадр</h4>
      <Toggle
        label="Вебка сверху"
        hint={splitAvailable ? "Лицо стримера над контентом" : "Вебка не найдена в этом видео"}
        checked={value.layout === "split"}
        disabled={disabled || !splitAvailable}
        onChange={(on) => onChange({ layout: on ? "split" : "single" })}
      />

      {/* Слежение — третья раскладка наравне со сплитом и подложкой.
          Взаимоисключающие: кадр может быть либо разрезан надвое, либо
          узким и ведомым за головой. */}
      <Toggle
        label="Следить за лицом"
        hint="Узкий кадр едет за головой — она всегда в центре"
        checked={value.layout === "track"}
        disabled={disabled}
        onChange={(on) => onChange({ layout: on ? "track" : "single" })}
      />

      <Toggle
        label="Лицо врезкой"
        hint={
          splitAvailable
            ? "Содержимое во весь экран, лицо окошком в углу"
            : "Вебка не найдена в этом видео"
        }
        checked={value.layout === "pip"}
        disabled={disabled || !splitAvailable}
        onChange={(on) => onChange({ layout: on ? "pip" : "single" })}
      />

      <Toggle
        label="Размытый фон"
        hint={value.layout === "split" ? "В раскладке со сплитом не нужен" : "Полосы сверху и снизу"}
        checked={value.background === "blur" && value.blur_sigma > 0}
        disabled={disabled || value.layout === "split"}
        onChange={(on) =>
          onChange(on ? { background: "blur", blur_sigma: 28 } : { background: "color" })
        }
      />

      {value.layout === "split" && (
        /* Настройки сплита показываются только когда он выбран: две полосы
           и приближение лица не значат ничего в других раскладках. */
        <div className="choice">
          <span className="choice-label">Насколько крупно лицо</span>
          <div className="segmented">
            {(faceZoom ?? []).map((item) => (
              <button
                key={item.name}
                type="button"
                title={item.note}
                className={Math.abs(value.face_zoom - item.zoom) < 0.05 ? "current" : ""}
                disabled={disabled}
                onClick={() => onChange({ face_zoom: item.zoom })}
              >
                {item.title}
              </button>
            ))}
          </div>
          <label className="slider">
            <span className="small dim">
              вплотную ← {value.face_zoom?.toFixed(1)} → с обстановкой
            </span>
            <input
              type="range"
              min={1.5}
              max={4}
              step={0.1}
              value={value.face_zoom ?? 2.6}
              disabled={disabled}
              onChange={(e) => onChange({ face_zoom: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Сколько ширин лица влезает в верхнюю полосу. Меньше — крупнее,
            но при движении головы лицо чаще уходит за край.
          </span>

          <label className="slider">
            <span className="small dim">
              Высота полосы с лицом: {Math.round((value.split_top_share ?? 0.34) * 100)}% кадра
            </span>
            <input
              type="range"
              min={0.2}
              max={0.5}
              step={0.02}
              value={value.split_top_share ?? 0.34}
              disabled={disabled}
              onChange={(e) => onChange({ split_top_share: Number(e.target.value) })}
            />
          </label>

          <label className="slider">
            <span className="small dim">
              Лицо по высоте полосы: {Math.round((value.face_vertical ?? 0.45) * 100)}%
            </span>
            <input
              type="range"
              min={0.2}
              max={0.7}
              step={0.05}
              value={value.face_vertical ?? 0.45}
              disabled={disabled}
              onChange={(e) => onChange({ face_vertical: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Чуть выше середины — в кадр входят плечи, а не пустота над головой.
          </span>
        </div>
      )}

      {value.layout === "track" && (
        <div className="choice">
          <span className="choice-label">Насколько цепко держать лицо</span>
          <label className="slider">
            <span className="small dim">
              плавно ← {(value.track_smoothing ?? 0.6).toFixed(2)} → цепко
            </span>
            <input
              type="range"
              min={0.05}
              max={1}
              step={0.05}
              value={value.track_smoothing ?? 0.6}
              disabled={disabled}
              onChange={(e) => onChange({ track_smoothing: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Цепко — лицо всегда в центре, рамка повторяет каждое движение.
            Плавно — кадр спокойнее, но лицо гуляет по экрану.
          </span>

          <label className="slider">
            <span className="small dim">
              зона покоя {Math.round((value.track_dead_zone ?? 0) * 100)}% кадра
            </span>
            <input
              type="range"
              min={0}
              max={0.4}
              step={0.05}
              value={value.track_dead_zone ?? 0}
              disabled={disabled}
              onChange={(e) => onChange({ track_dead_zone: Number(e.target.value) })}
            />
          </label>
          <span className="choice-hint">
            Пока лицо внутри зоны, рамка не двигается вовсе. Ноль — держать
            в центре всегда.
          </span>
        </div>
      )}

      <h4 className="group-title">Тонкая настройка</h4>

      {detectors && (
        <label className="choice">
          <span className="choice-label">Чем искать лицо</span>
          <select
            value={value.detector_backend ?? detectors.selected}
            disabled={disabled}
            onChange={(e) => onChange({ detector_backend: e.target.value })}
          >
            {detectors.backends.map((b) => (
              // Нереализованные показываются, но выбрать их нельзя: заглушка,
              // притворяющаяся рабочей, хуже её отсутствия.
              <option key={b.name} value={b.name} disabled={!b.available}>
                {b.label} · {b.license}
                {b.available ? "" : " — не готов"}
              </option>
            ))}
          </select>
          <span className="choice-hint">
            {detectors.backends.find(
              (b) => b.name === (value.detector_backend ?? detectors.selected),
            )?.note}
          </span>
        </label>
      )}

      {encoders && (
        <label className="choice">
          <span className="choice-label">Чем сжимать видео</span>
          <select
            value={value.encoder ?? encoders.selected}
            disabled={disabled}
            onChange={(e) => onChange({ encoder: e.target.value })}
          >
            {encoders.encoders.map((e) => (
              <option key={e.name} value={e.name} disabled={!e.available}>
                {e.label}
                {e.available ? "" : " — недоступно"}
              </option>
            ))}
          </select>
          {/* Названы обе стороны: выбор между качеством и скоростью не имеет
              однозначно верного ответа, и решать его должен человек. */}
          {(() => {
            const chosen = encoders.encoders.find(
              (e) => e.name === (value.encoder ?? encoders.selected),
            );
            if (!chosen) return null;
            return (
              <span className="choice-hint">
                <b>плюс:</b> {chosen.pros}
                <br />
                <b>минус:</b> {chosen.cons}
                {chosen.available ? "" : ` · ${chosen.note}`}
              </span>
            );
          })()}
        </label>
      )}
    </div>
  );
}

type ToggleProps = {
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
};

/**
 * Переключатель со скрытым чекбоксом.
 *
 * Скрыт визуально, но остаётся в разметке: так работают клавиатура,
 * экранные читалки и нажатие по всей строке, а рисовать можно что угодно.
 */
function Toggle({ label, hint, checked, disabled, onChange }: ToggleProps) {
  return (
    <label className={`toggle${disabled ? " off" : ""}`}>
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="toggle-track" aria-hidden="true">
        <span className="toggle-knob" />
      </span>
      <span className="toggle-text">
        <span className="toggle-label">{label}</span>
        {hint && <span className="toggle-hint">{hint}</span>}
      </span>
    </label>
  );
}
