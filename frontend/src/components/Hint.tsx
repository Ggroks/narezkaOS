import { useId, useState } from "react";

/**
 * Значок «i» с пояснением при наведении.
 *
 * Пояснение прячется, а не пишется рядом, по простой причине: текст возле
 * каждой настройки превращает панель в стену слов, и читать перестают всё,
 * включая важное. Спрятанное объяснение спрашивают тогда, когда оно нужно.
 *
 * Открывается и по наведению, и по фокусу с клавиатуры: без второго пояснение
 * недоступно тем, кто не пользуется мышью, — а это ровно те люди, которым
 * подсказка нужнее всего.
 */

type Props = {
  /** Что делает настройка — обычными словами, без технических терминов. */
  children: React.ReactNode;
  /** Где показать плашку: снизу по умолчанию, сверху — если места нет. */
  side?: "top" | "bottom";
  /**
   * По центру значка или от его левого края. `start` — для узких мест
   * у края окна: по центру плашка вылезает за границу и висит в пустоте.
   */
  align?: "center" | "start";
};

export function Hint({ children, side = "bottom", align = "center" }: Props) {
  const [open, setOpen] = useState(false);
  const id = useId();

  return (
    <span className="hint">
      <button
        type="button"
        className="hint-mark"
        aria-label="Что это"
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        // Нажатие оставлено намеренно: на сенсорном экране наведения нет,
        // и без него подсказка была бы недоступна с телефона.
        onClick={(e) => {
          e.preventDefault();
          setOpen((was) => !was);
        }}
      >
        i
      </button>
      {open && (
        <span id={id} role="tooltip" className={`hint-body ${side} ${align}`}>
          {children}
        </span>
      )}
    </span>
  );
}

/**
 * Строка настройки: подпись, значок пояснения и сам переключатель.
 *
 * Отдельным компонентом, потому что выравнивание подписи с элементом
 * управления — то место, где интерфейс рассыпается первым: у каждой
 * настройки свой отступ, и вместе они выглядят случайно расставленными.
 */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {hint && <Hint>{hint}</Hint>}
      </span>
      <span className="field-control">{children}</span>
    </label>
  );
}
