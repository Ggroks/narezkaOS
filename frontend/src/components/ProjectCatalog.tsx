import { useEffect, useRef, useState } from "react";
import {
  api,
  formatDate,
  formatDuration,
  type ProjectState,
  type VideoSummary,
} from "../api";
import { Hint } from "./Hint";
import { stageTitle } from "../stages";

/**
 * Каталог проектов — вход в программу.
 *
 * **Почему карточки, а не строки списка.** Записи различают глазами: по кадру
 * из середины и по названию. Строка с идентификатором `a3f19c2b` не говорит
 * ничего, и прежний список требовал открыть запись, чтобы понять, какая это.
 *
 * **Почему состояние на карточке.** Обработка идёт минутами и часами, и
 * главный вопрос в каталоге — «где что уже готово». Поэтому состояние и
 * полоса хода работы стоят на самой карточке, а не открываются внутри.
 *
 * **О слове «проект».** Здесь запись называется проектом, в API и в
 * хранилище — видео. Расхождение намеренное, объяснение в
 * `narezka/core/registry.py`.
 */

type Props = {
  projects: VideoSummary[];
  onOpen: (videoId: string) => void;
  onChanged: () => void;
};

const STATE_LABEL: Record<ProjectState, string> = {
  draft: "черновик",
  started: "в работе",
  processing: "идёт обработка",
  ready: "готов",
  failed: "ошибка",
};

const STATE_BADGE: Record<ProjectState, string> = {
  draft: "",
  started: "",
  processing: "run",
  ready: "ok",
  failed: "bad",
};

export function ProjectCatalog({ projects, onOpen, onChanged }: Props) {
  const [dialog, setDialog] = useState<null | "url" | "file">(null);
  const [error, setError] = useState<string | null>(null);

  async function create(payload: { url?: string; file?: string; title?: string }) {
    const { video_id } = await api.addVideo(payload);
    onChanged();
    onOpen(video_id);
  }

  async function remove(project: VideoSummary) {
    if (!confirm(`Удалить «${project.title}» и всё, что уже обработано?`)) return;
    try {
      await api.deleteVideo(project.video_id);
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function rename(project: VideoSummary, title: string) {
    try {
      await api.renameVideo(project.video_id, title);
      onChanged();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <div className="catalog">
      {/* Пустому каталогу шапка не нужна: приглашение говорит то же самое
          и сразу даёт, что делать. Заголовок над ним только повторяется. */}
      {projects.length > 0 && (
        <header className="catalog-head">
          <div className="catalog-title">
            <h2>Проекты</h2>
            <p className="small dim">
              {projects.length} {plural(projects.length, "запись", "записи", "записей")}
            </p>
          </div>
          <button className="primary" onClick={() => setDialog("url")}>
            Новый проект
          </button>
        </header>
      )}

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      {projects.length === 0 ? (
        <Invite onCreate={create} onPickFile={() => setDialog("file")} />
      ) : (
        <ul className="catalog-grid">
          {projects.map((project) => (
            <ProjectCard
              key={project.video_id}
              project={project}
              onOpen={() => onOpen(project.video_id)}
              onRename={(title) => rename(project, title)}
              onDelete={() => remove(project)}
            />
          ))}
        </ul>
      )}

      {dialog && (
        <NewProject
          initial={dialog}
          onCancel={() => setDialog(null)}
          onCreate={create}
        />
      )}
    </div>
  );
}

/** Приглашение вместо пустого экрана: поле ссылки прямо здесь. */
function Invite({
  onCreate,
  onPickFile,
}: {
  onCreate: (payload: { url?: string; title?: string }) => Promise<void>;
  onPickFile: () => void;
}) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onCreate({ url: url.trim() });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(false);
    }
  }

  return (
    <section className="invite">
      <h3>Начнём с записи</h3>
      <p className="dim">
        Вставьте ссылку на стрим или видео — программа скачает её, расшифрует
        речь и найдёт моменты, из которых выйдут ролики.
      </p>
      <form className="invite-form" onSubmit={submit}>
        <label className="grow">
          <span className="sr-only">Ссылка на запись</span>
          <input
            type="url"
            inputMode="url"
            autoFocus
            placeholder="Ссылка на стрим или видео"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
        <button className="primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? "Создаём…" : "Создать проект"}
        </button>
      </form>
      {error && (
        <div className="error" role="alert" style={{ marginTop: 12, marginBottom: 0 }}>
          {error}
        </div>
      )}
      <button className="ghost invite-alt" onClick={onPickFile}>
        Или взять файл с этого компьютера
      </button>
    </section>
  );
}

function ProjectCard({
  project,
  onOpen,
  onRename,
  onDelete,
}: {
  project: VideoSummary;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project.title);
  const [posterFailed, setPosterFailed] = useState(false);

  const running = project.state === "processing";
  const share = stageShare(project);

  function save() {
    setEditing(false);
    if (draft.trim() !== project.title) onRename(draft.trim());
  }

  return (
    <li className={`project${running ? " running" : ""}`}>
      <div className="project-poster">
        {project.poster_at != null && !posterFailed ? (
          <img
            src={api.frameUrl(project.video_id, project.poster_at, 240)}
            alt=""
            loading="lazy"
            onError={() => setPosterFailed(true)}
          />
        ) : (
          <span className="project-noposter" aria-hidden="true">
            {project.has_video === false ? "♪" : "▤"}
          </span>
        )}
        <span className={`badge ${STATE_BADGE[project.state]} project-state`}>
          {STATE_LABEL[project.state]}
        </span>
      </div>

      <div className="project-body">
        {editing ? (
          <input
            className="project-rename"
            autoFocus
            value={draft}
            aria-label="Название проекта"
            onChange={(event) => setDraft(event.target.value)}
            onBlur={save}
            onKeyDown={(event) => {
              if (event.key === "Enter") save();
              if (event.key === "Escape") {
                setDraft(project.title);
                setEditing(false);
              }
            }}
          />
        ) : (
          // Ссылка, а не кнопка: открытие проекта — навигация, и она должна
          // работать средней кнопкой и «открыть в новой вкладке». Растянута
          // на всю карточку псевдоэлементом, чтобы кликалась любая её часть,
          // но осталась одной остановкой при обходе с клавиатуры.
          <a
            className="project-name"
            href={`#/video/${project.video_id}`}
            onClick={(event) => {
              event.preventDefault();
              onOpen();
            }}
          >
            {project.title}
          </a>
        )}

        {project.named && project.source_title && (
          <p className="small dim project-source">{project.source_title}</p>
        )}

        <p className="small dim project-facts">
          <span>{formatDate(project.created_at)}</span>
          {project.duration_seconds != null && (
            <span className="tnum">{formatDuration(project.duration_seconds)}</span>
          )}
          {project.shorts > 0 && (
            <span className="tnum">
              {project.shorts} {plural(project.shorts, "ролик", "ролика", "роликов")}
            </span>
          )}
        </p>

        {(running || project.state === "started") && (
          <div className="project-progress">
            <div
              className="progress"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(share * 100)}
              aria-label="Ход обработки"
            >
              <span style={{ inlineSize: `${Math.max(share * 100, 2)}%` }} />
            </div>
            <p className="small dim">
              {running
                ? currentStep(project)
                : `сделано ${project.stages_done} из ${project.stages_total} шагов`}
            </p>
          </div>
        )}

        {project.state === "failed" && project.job?.error && (
          <p className="small project-failed" title={project.job.error}>
            {project.job.error}
          </p>
        )}
      </div>

      <div className="project-tools">
        <button
          className="icon ghost"
          aria-label={`Переименовать «${project.title}»`}
          onClick={() => {
            setDraft(project.title);
            setEditing(true);
          }}
        >
          <span aria-hidden="true">✎</span>
        </button>
        <button
          className="icon ghost danger"
          aria-label={`Удалить «${project.title}»`}
          disabled={running}
          title={running ? "Идёт обработка — сначала дождитесь конца" : undefined}
          onClick={onDelete}
        >
          <span aria-hidden="true">✕</span>
        </button>
      </div>
    </li>
  );
}

/** Диалог создания: название и один источник — ссылка либо файл. */
function NewProject({
  initial,
  onCancel,
  onCreate,
}: {
  initial: "url" | "file";
  onCancel: () => void;
  onCreate: (payload: { url?: string; file?: string; title?: string }) => Promise<void>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<"url" | "file">(initial);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Родное окно браузера, а не свой слой: оно само перехватывает Esc, держит
  // фокус внутри и возвращает его на прежнее место при закрытии — всё то,
  // что в самодельных «модалках» обычно забывают.
  useEffect(() => {
    ref.current?.showModal();
    // Фокус ставится руками: сам по себе он достаётся первому, до чего можно
    // добраться, а это значок пояснения перед полем — окно открывалось
    // с развёрнутой подсказкой поверх формы.
    nameRef.current?.focus();
  }, []);

  const value = source === "url" ? url.trim() : file.trim();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const name = title.trim() || undefined;
      await onCreate(source === "url" ? { url: value, title: name } : { file: value, title: name });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(false);
    }
  }

  return (
    <dialog className="sheet" ref={ref} onCancel={onCancel} onClose={onCancel}>
      <form onSubmit={submit}>
        <h3>Новый проект</h3>

        <label className="sheet-field">
          <span className="sheet-label">
            Название
            <Hint align="start">
              Как эта запись будет называться в каталоге. Можно не заполнять:
              подставится заголовок из источника, а переименовать получится
              в любой момент.
            </Hint>
          </span>
          <input
            ref={nameRef}
            placeholder="Например: стрим 14 августа"
            maxLength={200}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>

        <div className="sheet-field">
          <span className="sheet-label">Откуда взять запись</span>
          <div className="segmented" role="group" aria-label="Источник записи">
            <button
              type="button"
              className={source === "url" ? "current" : ""}
              aria-pressed={source === "url"}
              onClick={() => setSource("url")}
            >
              Ссылка
            </button>
            <button
              type="button"
              className={source === "file" ? "current" : ""}
              aria-pressed={source === "file"}
              onClick={() => setSource("file")}
            >
              Файл на компьютере
            </button>
          </div>
        </div>

        {source === "url" ? (
          <label className="sheet-field">
            <span className="sheet-label">
              Ссылка
              <Hint align="start">
                Стрим с Twitch или видео с YouTube. Запись скачается сама.
              </Hint>
            </span>
            <input
              type="url"
              inputMode="url"
              placeholder="https://twitch.tv/videos/…"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </label>
        ) : (
          <label className="sheet-field">
            <span className="sheet-label">
              Путь к файлу
              <Hint align="start">
                Путь целиком, например /home/имя/видео/стрим.mp4. Именно путь,
                а не кнопка выбора: браузер её настоящий путь не выдаёт, а
                перекладывать через него запись на десятки гигабайт — часы
                ожидания там, где хватает одной строки. Файл остаётся на
                месте, программа только ссылается на него.
              </Hint>
            </span>
            <input
              placeholder="/home/…/stream.mp4"
              value={file}
              onChange={(event) => setFile(event.target.value)}
            />
          </label>
        )}

        {error && (
          <div className="error" role="alert" style={{ marginBottom: 0 }}>
            {error}
          </div>
        )}

        <div className="sheet-actions">
          <button type="button" className="ghost" onClick={onCancel}>
            Отмена
          </button>
          <button className="primary" type="submit" disabled={busy || !value}>
            {busy ? "Создаём…" : "Создать"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/** Доля пройденного: шаги плюс кусочек текущего, если он умеет считать. */
function stageShare(project: VideoSummary): number {
  const total = project.stages_total || 1;
  const inside =
    project.job?.done != null && project.job?.total
      ? project.job.done / project.job.total
      : 0;
  return Math.min((project.stages_done + inside) / total, 1);
}

function currentStep(project: VideoSummary): string {
  const job = project.job;
  if (!job) return "обработка идёт…";
  const name = stageTitle(job.stage) || "обработка";
  if (job.done != null && job.total) return `${name}: ${job.done} из ${job.total}`;
  return job.note ? `${name}: ${job.note}` : `${name}…`;
}

function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
