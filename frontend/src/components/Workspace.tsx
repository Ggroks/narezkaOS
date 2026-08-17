import { useEffect, useState } from "react";

/**
 * Среда монтажа: вкладки слева-сверху, работа по центру, стадии справа.
 *
 * **Почему вкладки, а не всё сразу.** Раньше страница видео показывала
 * исходник, стадии, обзор моментов, настройки, ролики и длинную нарезку
 * одним потоком. Человек листал мимо девяти блоков, чтобы дойти до нужного,
 * и не понимал, что из этого он уже сделал. Вкладка отвечает на вопрос
 * «где я» одним взглядом.
 *
 * **Почему стадии справа и скрываются.** Ход обработки нужен постоянно, но
 * не всё время: пока идёт работа — важен, когда закончилась — мешает. Панель
 * помнит своё состояние между заходами, потому что это личная привычка,
 * а не настройка проекта.
 */

export type TabId =
  | "source"
  | "moments"
  | "framing"
  | "shorts"
  | "long"
  | "results";

export type Tab = {
  id: TabId;
  label: string;
  /** Сколько всего внутри: цифра рядом с названием отвечает «есть ли там что-то». */
  count?: number;
  /** Недоступна, пока не сделано предыдущее. Подсказка объясняет почему. */
  blockedBy?: string;
};

type Props = {
  tabs: Tab[];
  active: TabId;
  onSelect: (id: TabId) => void;
  title: string;
  subtitle?: string;
  /** Действия шапки: запуск обработки и прочее. */
  actions?: React.ReactNode;
  /** Содержимое панели стадий. */
  stages?: React.ReactNode;
  children: React.ReactNode;
};

export function Workspace({
  tabs, active, onSelect, title, subtitle, actions, stages, children,
}: Props) {
  const [openStages, setOpenStages] = useState(
    () => localStorage.getItem("stages-open") !== "0",
  );

  useEffect(() => {
    localStorage.setItem("stages-open", openStages ? "1" : "0");
  }, [openStages]);

  return (
    <div className={`workspace${openStages && stages ? " with-stages" : ""}`}>
      <header className="workspace-head">
        <div className="workspace-title">
          <h1>{title}</h1>
          {subtitle && <p className="small dim">{subtitle}</p>}
        </div>
        <div className="workspace-actions">
          {actions}
          {stages && (
            <button
              type="button"
              className="ghost"
              aria-expanded={openStages}
              onClick={() => setOpenStages((was) => !was)}
            >
              {openStages ? "Скрыть ход работы" : "Ход работы"}
            </button>
          )}
        </div>
      </header>

      <nav className="tabs" aria-label="Разделы проекта">
        {tabs.map((tab) => {
          const blocked = Boolean(tab.blockedBy);
          return (
            <button
              key={tab.id}
              type="button"
              className={`tab${active === tab.id ? " current" : ""}${blocked ? " blocked" : ""}`}
              aria-current={active === tab.id ? "page" : undefined}
              disabled={blocked}
              title={tab.blockedBy}
              onClick={() => onSelect(tab.id)}
            >
              {tab.label}
              {tab.count != null && tab.count > 0 && (
                <span className="tab-count tnum">{tab.count}</span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="workspace-body">
        <main className="workspace-main">{children}</main>
        {stages && openStages && <aside className="workspace-stages">{stages}</aside>}
      </div>
    </div>
  );
}
