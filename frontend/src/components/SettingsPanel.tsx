import type { DetectorsInfo, Framing, ModelsInfo } from "../api";

type Props = {
  value: Framing;
  onChange: (patch: Partial<Framing>) => void;
  /** Пересчёт отбора, а не только сборки: настройка выше по пайплайну. */
  onReanalyse?: () => void;
  /** Раскладка «сплит» доступна только там, где найдена вебка наложением. */
  splitAvailable: boolean;
  models?: ModelsInfo | null;
  detectors?: DetectorsInfo | null;
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
  value, onChange, onReanalyse, splitAvailable, disabled, models, detectors,
}: Props) {
  return (
    <div className="options">
      <Toggle
        label="Субтитры"
        hint="Вшиваются в кадр, с подсветкой слова"
        checked={value.subtitles_enabled}
        disabled={disabled}
        onChange={(subtitles_enabled) => onChange({ subtitles_enabled })}
      />

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

      {/* Настройка выше по пайплайну: меняет отбор моментов, а не сборку,
          поэтому и пересчитывать надо с отбора. */}
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
