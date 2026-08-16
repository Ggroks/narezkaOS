import { useEffect, useRef, useState } from "react";
import { ChevronDown, Circle, Cpu, Loader2 } from "lucide-react";

/**
 * Обвязка приложения: три яруса, как в настольной программе.
 *
 * Меню сверху (32) — редкие действия. Рельс этапов (52) — где человек сейчас
 * и что дальше. Статусная строка снизу (28) — очередь и свойства исходника,
 * одинаковая на всех экранах, поэтому служит якорем.
 *
 * Боковой панели нет намеренно: продукт — последовательность из шести шагов,
 * а не набор разделов. Постоянное меню слева подталкивает прыгать между
 * разделами, а здесь прыгать некуда — есть только следующий шаг.
 */

export type StageState = "waiting" | "running" | "done" | "stale" | "locked";

export type StageInfo = {
  id: string;
  title: string;
  state: StageState;
  /** Почему этап недоступен — показывается подсказкой, а не молчанием. */
  blockedBy?: string;
};

const STATE_TEXT: Record<StageState, string> = {
  waiting: "ожидает",
  running: "идёт",
  done: "готово",
  stale: "нужен пересчёт",
  locked: "недоступно",
};

export const THEMES = [
  { id: "hardware", label: "аппаратная" },
  { id: "glass", label: "стекло" },
  { id: "workshop", label: "светлый цех" },
] as const;

type MenuItem = { label: string; hint?: string; onSelect?: () => void };

function Menu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="menu" ref={box}>
      <button
        type="button"
        className={`menu-title${open ? " open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        {label}
      </button>
      {open && (
        <div className="menu-drop" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className="menu-row"
              disabled={!item.onSelect}
              onClick={() => {
                item.onSelect?.();
                setOpen(false);
              }}
            >
              <span>{item.label}</span>
              {item.hint && <kbd>{item.hint}</kbd>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type Props = {
  projectName?: string | null;
  stages?: StageInfo[];
  activeStage?: string;
  onStage?: (id: string) => void;
  /** Первичное действие экрана. Оно единственное — второго акцента нет. */
  primary?: { label: string; onClick: () => void; disabled?: boolean };
  /** Панель фильтров вместо рельса — на экране каталога. */
  filters?: React.ReactNode;
  queue?: { waiting: number; running: number; etaMinutes?: number };
  source?: { duration?: string; size?: string; fps?: number } | null;
  menus?: Record<string, MenuItem[]>;
  children: React.ReactNode;
};

export function Chrome({
  projectName, stages, activeStage, onStage, primary, filters, queue, source, menus, children,
}: Props) {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("theme") || "hardware",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  const defaultMenus: Record<string, MenuItem[]> = {
    Файл: [{ label: "новый проект", hint: "Ctrl N" }, { label: "открыть папку хранилища" }],
    Проект: [{ label: "дублировать" }, { label: "в архив" }, { label: "удалить" }],
    Вид: [{ label: "свернуть панель этапов" }],
    Справка: [{ label: "горячие клавиши", hint: "?" }],
  };

  return (
    <div className="shell">
      <div className="bar-menu">
        <span className="brand" aria-label="Narezka OS">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">narezka</span>
        </span>

        {Object.entries(menus ?? defaultMenus).map(([label, items]) => (
          <Menu key={label} label={label} items={items} />
        ))}

        {projectName && <span className="project-name">{projectName}</span>}

        <label className="theme-pick">
          <span className="sr-only">Тема оформления</span>
          <select value={theme} onChange={(e) => setTheme(e.target.value)}>
            {THEMES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <ChevronDown size={12} aria-hidden="true" />
        </label>
      </div>

      <div className="bar-rail">
        {filters ?? (
          <nav className="stages" aria-label="Этапы обработки">
            {stages?.map((stage, index) => {
              const locked = stage.state === "locked";
              return (
                <button
                  key={stage.id}
                  type="button"
                  className={`stage-tab${activeStage === stage.id ? " current" : ""}${
                    locked ? " locked" : ""
                  }`}
                  disabled={locked}
                  title={locked ? stage.blockedBy : undefined}
                  aria-current={activeStage === stage.id ? "step" : undefined}
                  onClick={() => onStage?.(stage.id)}
                >
                  <span className="stage-no tnum">{index + 1}</span>
                  <span className="stage-body">
                    <span className="stage-title">{stage.title}</span>
                    <span className={`stage-state ${stage.state}`}>
                      {stage.state === "running" && (
                        <Loader2 size={10} className="spin" aria-hidden="true" />
                      )}
                      {STATE_TEXT[stage.state]}
                    </span>
                  </span>
                </button>
              );
            })}
          </nav>
        )}

        {primary && (
          <button
            type="button"
            className="primary-action"
            disabled={primary.disabled}
            onClick={primary.onClick}
          >
            {primary.label}
          </button>
        )}
      </div>

      <main className="work" id="content">
        {children}
      </main>

      <div className="bar-status">
        <button type="button" className="queue">
          {queue && (queue.running || queue.waiting) ? (
            <>
              <Circle size={7} className="pulse" aria-hidden="true" />
              {queue.waiting} в очереди · {queue.running} рендерится
              {queue.etaMinutes ? ` · ~${queue.etaMinutes} мин` : ""}
            </>
          ) : (
            "очередь пуста"
          )}
        </button>

        <span className="source-facts tnum">
          {source ? (
            <>
              {source.duration ?? "—"} · {source.size ?? "—"}
              {source.fps ? ` · ${source.fps} fps` : ""}
            </>
          ) : (
            <>
              <Cpu size={11} aria-hidden="true" /> локально
            </>
          )}
        </span>
      </div>
    </div>
  );
}
