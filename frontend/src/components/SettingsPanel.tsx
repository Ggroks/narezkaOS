import type { DetectorsInfo, EncodersInfo, Framing, ModelsInfo } from "../api";

type Props = {
  value: Framing;
  onChange: (patch: Partial<Framing>) => void;
  /** Пересчёт отбора, а не только сборки: настройка выше по пайплайну. */
  onReanalyse?: () => void;
  /** Раскладка «сплит» доступна только там, где найдена вебка наложением. */
  splitAvailable: boolean;
  models?: ModelsInfo | null;
  detectors?: DetectorsInfo | null;
  encoders?: EncodersInfo | null;
  disabled?: boolean;
};

/**
 * Переключатели того, что попадёт в готовый ролик.
 *
 * Каждая возможность отключается отдельно и по умолчанию включена. Смысл
 * в том, чтобы нарезка оставалась полезной и в неполном составе: кому-то
 * нужен ролик без субтитров под свой монтаж, кому-то — без нормализации
 * громкости, потому что звук уже сведён.
 */
export function SettingsPanel({
  value, onChange, onReanalyse, splitAvailable, disabled, models, detectors, encoders,
}: Props) {
  return (
    <div className="options">
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

      {/* Сигналы поиска моментов. Материал бывает разный: на музыкальном
          стриме громкость ровная и ничего не различает, на записи без чата
          чат бесполезен. Выключенный сигнал исключается из расчёта, а не
          обнуляется — иначе он тянул бы оценку вниз как измеренный ноль. */}
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
      </div>

      {/* Теги звука. Смех полезен почти всем, музыка — тем, кто публикует
          ролики и рискует правами на неё, аплодисменты и толпа осмысленны
          лишь на записях с залом. Считать то, чем не пользуются, значит
          платить временем за ничто. */}
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

      <h4 className="group-title">Что войдёт в ролик</h4>
      <Toggle
        label="Субтитры"
        hint="Вшиваются в кадр, с подсветкой слова"
        checked={value.subtitles_enabled}
        disabled={disabled}
        onChange={(subtitles_enabled) => onChange({ subtitles_enabled })}
      />

      <h4 className="group-title">Как показать кадр</h4>
      <Toggle
        label="Вебка сверху"
        hint={
          splitAvailable
            ? "Лицо стримера над контентом"
            : "Вебка не найдена в этом видео"
        }
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

      {/* Настройка выше по пайплайну: меняет отбор моментов, а не сборку,
          поэтому и пересчитывать надо с отбора. */}
      <h4 className="group-title">Тонкая настройка</h4>
      <Toggle
        label="Пропускать приветствия"
        hint="В начале записи здороваются, а не реагируют"
        checked={value.chat_ignore_start}
        disabled={disabled}
        onChange={(chat_ignore_start) => {
          onChange({ chat_ignore_start });
          onReanalyse?.();
        }}
      />

      <Toggle
        label="Ровная громкость"
        hint="Приводит все клипы к одному уровню"
        checked={value.loudnorm_enabled}
        disabled={disabled}
        onChange={(loudnorm_enabled) => onChange({ loudnorm_enabled })}
      />

      {models && models.models.length > 0 && (
        <label className="choice">
          <span className="choice-label">Модель отбора</span>
          <select
            value={value.llm_model ?? models.selected ?? ""}
            disabled={disabled}
            onChange={(e) => {
              onChange({ llm_model: e.target.value });
              onReanalyse?.();
            }}
          >
            {models.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id.split("/").pop()}
                {m.free ? " · бесплатно" : ""}
              </option>
            ))}
          </select>
          <span className="choice-hint">
            {models.provider} · смена модели пересчитывает отбор
          </span>
        </label>
      )}

      {detectors && (
        <label className="choice">
          <span className="choice-label">Компьютерное зрение</span>
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

      <Toggle
        label="Размытый фон"
        hint={value.layout === "split" ? "В раскладке со сплитом не нужен" : "Полосы сверху и снизу"}
        checked={value.background === "blur" && value.blur_sigma > 0}
        disabled={disabled || value.layout === "split"}
        onChange={(on) =>
          onChange(on ? { background: "blur", blur_sigma: 28 } : { background: "color" })
        }
      />
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
