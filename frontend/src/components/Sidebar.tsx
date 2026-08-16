import type { Health } from "../api";

/**
 * Рабочая панель слева.
 *
 * Раньше всё лежало в одной куче: заголовок, сведения о машине, список видео
 * и настройки шли одним потоком сверху вниз. Панель забирает постоянное —
 * навигацию, состояние машины, общие действия, — и оставляет основной области
 * только то, над чем человек работает прямо сейчас.
 *
 * Сворачивается до полосы со значками: на монтаже ширина экрана дорога,
 * а держать список разделов открытым нужно не всегда.
 */

type View = "library" | "video";

type Props = {
  view: View;
  videoTitle?: string | null;
  health: Health | null;
  collapsed: boolean;
  onToggle: () => void;
  onHome: () => void;
  /** Сколько видео в работе — на панели видно, не заходя в библиотеку. */
  count: number;
};

/** Значки рисуются здесь же: внешние наборы тянут шрифт ради десятка глифов. */
const ICONS = {
  library: "M4 5h5v14H4zM10.5 5h5v14h-5zM17 6.5l3 1-3.5 12.5-2.8-1z",
  clip: "M6 3v10.5a3.5 3.5 0 1 0 2 3.35V7h8V3zM17 12a3 3 0 1 1 0 6 3 3 0 0 1 0-6z",
  plus: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z",
  chip: "M9 3v2H7a2 2 0 0 0-2 2v2H3v2h2v2H3v2h2v2a2 2 0 0 0 2 2h2v2h2v-2h2v2h2v-2h2a2 2 0 0 0 2-2v-2h2v-2h-2v-2h2V9h-2V7a2 2 0 0 0-2-2h-2V3h-2v2h-2V3zm0 6h6v6H9z",
  panel: "M4 4h16v16H4zm2 2v12h4V6zm6 0v12h6V6z",
} as const;

function Icon({ path, size = 20 }: { path: string; size?: number }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <path d={path} fill="currentColor" />
    </svg>
  );
}

export function Sidebar({
  view, videoTitle, health, collapsed, onToggle, onHome, count,
}: Props) {
  return (
    <aside className={`rail${collapsed ? " collapsed" : ""}`}>
      <div className="rail-brand">
        <span className="mark" aria-hidden="true">
          <Icon path={ICONS.clip} size={22} />
        </span>
        <span className="wordmark">
          Narezka<b>OS</b>
        </span>
        <button
          type="button"
          className="rail-toggle"
          onClick={onToggle}
          aria-label={collapsed ? "Развернуть панель" : "Свернуть панель"}
          aria-expanded={!collapsed}
        >
          <Icon path={ICONS.panel} size={16} />
        </button>
      </div>

      {/* Добавление видео — единственное действие, нужное из любого места,
          поэтому оно на панели, а не только на странице библиотеки. */}
      <button type="button" className="rail-action" onClick={onHome} title="Добавить видео">
        <Icon path={ICONS.plus} size={18} />
        <span className="rail-text">Добавить видео</span>
      </button>

      <nav className="rail-nav" aria-label="Разделы">
        <button
          type="button"
          className={`rail-item${view === "library" ? " current" : ""}`}
          onClick={onHome}
          aria-current={view === "library" ? "page" : undefined}
        >
          <Icon path={ICONS.library} />
          <span className="rail-text">Библиотека</span>
          {count > 0 && <span className="rail-count tnum">{count}</span>}
        </button>

        {/* Открытое видео — не отдельный раздел, а состояние: показывается
            только когда оно есть, и названием, а не безликим «Видео». */}
        {view === "video" && (
          <span className="rail-item current as-status" aria-current="page">
            <Icon path={ICONS.clip} />
            <span className="rail-text" title={videoTitle ?? undefined}>
              {videoTitle || "Без названия"}
            </span>
          </span>
        )}
      </nav>

      {health && (
        <div className="rail-foot">
          <div className="rail-machine">
            <Icon path={ICONS.chip} size={16} />
            <span className="rail-text">
              <b>{health.device.kind === "cuda" ? "видеокарта" : "процессор"}</b>
              <span className="dim">{health.device.name}</span>
            </span>
          </div>
          <span className={`rail-health${health.ok ? "" : " warn"}`}>
            {health.ok ? "готово" : "есть замечания"} · профиль {health.profile}
          </span>
        </div>
      )}
    </aside>
  );
}
