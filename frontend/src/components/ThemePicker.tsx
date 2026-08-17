import { useEffect, useState } from "react";

/**
 * Выбор темы оформления.
 *
 * Кружок цвета, а не название: «графит» и «чернила» ничего не говорят, пока
 * их не увидишь, а образец показывает результат сразу. Название остаётся
 * подписью для тех, кто пользуется клавиатурой или читалкой экрана.
 */

export const THEMES = [
  { id: "studio", label: "Студийная", swatch: "oklch(0.21 0.009 265)", dot: "oklch(0.72 0.155 250)" },
  { id: "graphite", label: "Графит", swatch: "oklch(0.20 0.007 60)", dot: "oklch(0.78 0.140 70)" },
  { id: "ink", label: "Чернила", swatch: "oklch(0.16 0.005 265)", dot: "oklch(0.70 0.160 250)" },
  { id: "daylight", label: "Дневная", swatch: "oklch(0.97 0.003 265)", dot: "oklch(0.55 0.180 250)" },
] as const;

export function ThemePicker() {
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "studio");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  return (
    <div className="themes" role="radiogroup" aria-label="Тема оформления">
      {THEMES.map((item) => (
        <button
          key={item.id}
          type="button"
          role="radio"
          aria-checked={theme === item.id}
          aria-label={item.label}
          title={item.label}
          className={`theme-dot${theme === item.id ? " current" : ""}`}
          style={{ background: item.swatch }}
          onClick={() => setTheme(item.id)}
        >
          <span className="theme-accent" style={{ background: item.dot }} />
        </button>
      ))}
    </div>
  );
}
