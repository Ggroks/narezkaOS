import { useEffect, useState } from "react";
import { PREVIEW_SLOT_ID } from "./PreviewSlot";

/**
 * Среда монтажа: предпросмотр слева, работа по центру, ход обработки справа.
 *
 * **Почему вкладки, а не всё сразу.** Раньше страница видео показывала
 * исходник, стадии, обзор моментов, настройки, ролики и длинную нарезку
 * одним потоком. Человек листал мимо девяти блоков, чтобы дойти до нужного,
 * и не понимал, что из этого он уже сделал. Вкладка отвечает на вопрос
 * «где я» одним взглядом.
 *
 * **Почему предпросмотр отдельной полосой.** Он нужен на всех вкладках сразу:
 * момент из обзора, кадр с субтитрами, рамка, готовый ролик. Пока каждый жил
 * внутри своей панели, вертикальный кадр 9:16 ютился в широкой колонке и
 * выходил маленьким, а при переходе на другую вкладку исчезал вовсе. Полоса
 * держит пропорцию ролика и не двигается, что бы человек ни настраивал.
 *
 * **Почему ход работы справа и сворачивается.** Он нужен постоянно, но не всё
 * время: пока идёт работа — важен, когда закончилась — мешает. Свернуть его
 * можно стрелкой на самой полосе: кнопка в шапке занимала место у названия
 * записи и уводила взгляд от того, чем управляет. Панель помнит своё
 * состояние между заходами, потому что это личная привычка, а не настройка
 * проекта.
 */

/**
 * Вкладки идут по порядку работы: разобрать запись → отсмотреть моменты →
 * собрать ролики → собрать длинную нарезку → занести результаты.
 *
 * Кадрирование было отдельной вкладкой и уехало в «Короткие видео»:
 * настраивать рамку отдельно от того, что в ролик войдёт, незачем — это
 * одно решение, разнесённое по двум экранам.
 */
export type TabId =
  | "source"
  | "moments"
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
  /** Действия шапки: возврат к проектам и прочее. */
  actions?: React.ReactNode;
  /** Содержимое панели стадий. */
  stages?: React.ReactNode;
  /** Идёт ли работа: свёрнутая полоса должна это показывать. */
  running?: boolean;
  /** Заголовок над предпросмотром — что именно в нём сейчас показано. */
  previewTitle?: string;
  children: React.ReactNode;
};

export function Workspace({
  tabs, active, onSelect, title, subtitle, actions, stages, running, previewTitle, children,
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
        <div className="workspace-actions">{actions}</div>
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
        {/* Полоса стоит всегда, даже пустой: если она то появляется, то
            исчезает, соседняя колонка прыгает при каждом переключении. */}
        <aside className="workspace-preview" aria-label="Предпросмотр">
          <div className="preview-head small dim">{previewTitle ?? "Предпросмотр"}</div>
          <div id={PREVIEW_SLOT_ID} className="preview-frame" />
        </aside>

        <main className="workspace-main">{children}</main>

        {stages && (
          <aside className={`workspace-stages${openStages ? "" : " collapsed"}`}>
            {/* Стрелка сидит на самой полосе и всегда на виду: чтобы свернуть
                ход работы, не нужно искать кнопку в шапке. Направление
                показывает, что произойдёт, а не то, что сейчас. */}
            <button
              type="button"
              className="stages-handle"
              aria-expanded={openStages}
              aria-label={openStages ? "Свернуть ход работы" : "Развернуть ход работы"}
              title={openStages ? "Свернуть ход работы" : "Развернуть ход работы"}
              onClick={() => setOpenStages((was) => !was)}
            >
              <span aria-hidden="true">{openStages ? "›" : "‹"}</span>
            </button>
            {openStages ? (
              <div className="stages-body">{stages}</div>
            ) : (
              // Свёрнутая полоса — не пустая: работа могла идти, и знать об
              // этом нужно, не разворачивая.
              <div className="stages-rail" aria-hidden="true">
                <span className={`rail-dot${running ? " run" : ""}`} />
                <span className="rail-label">ХОД РАБОТЫ</span>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
