import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Billing, type Health, type VideoSummary, type Whoami } from "./api";
import { ThemePicker } from "./components/ThemePicker";
import { LoginScreen } from "./components/LoginScreen";
import { ProjectCatalog } from "./components/ProjectCatalog";
import { VideoDetail } from "./components/VideoDetail";

/** Маршрут в хэше: перезагрузка страницы возвращает туда же (§69). */
function useHashRoute(): [string | null, (id: string | null) => void] {
  const [videoId, setVideoId] = useState<string | null>(
    () => window.location.hash.replace(/^#\/?video\//, "") || null,
  );

  useEffect(() => {
    const onChange = () => {
      const match = window.location.hash.match(/^#\/video\/(.+)$/);
      setVideoId(match ? match[1] : null);
    };
    window.addEventListener("hashchange", onChange);
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((id: string | null) => {
    window.location.hash = id ? `/video/${id}` : "/";
  }, []);

  return [videoId, navigate];
}

/** Как часто обновлять каталог, пока хоть что-то обрабатывается. */
const POLL_MS = 3000;

export function App() {
  const [videoId, navigate] = useHashRoute();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [who, setWho] = useState<Whoami | null>(null);
  const [money, setMoney] = useState<Billing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setVideos(await api.videos());
      setError(null);
    } catch (exc) {
      setError(
        exc instanceof Error && exc.message.includes("Failed to fetch")
          ? "API не отвечает. Запустите его: narezka serve"
          : String(exc),
      );
    }
  }, []);

  // Сначала «кто я», потом всё остальное: при включённом входе список
  // записей всё равно ответит 401, и незачем показывать ошибку вместо
  // формы входа.
  useEffect(() => {
    api
      .me()
      .then((state) => {
        setWho(state);
        if (!state.auth_required || state.user) {
          void refresh();
          api.health().then(setHealth).catch(() => setHealth(null));
          api.billing().then(setMoney).catch(() => setMoney(null));
        }
      })
      .catch(() => setWho({ auth_required: false, allow_signup: false, user: null }));
  }, [refresh]);

  const entered = useCallback(
    (state: Whoami) => {
      setWho(state);
      void refresh();
      api.health().then(setHealth).catch(() => setHealth(null));
      api.billing().then(setMoney).catch(() => setMoney(null));
    },
    [refresh],
  );

  async function leave() {
    await api.logout().catch(() => undefined);
    setWho((current) => (current ? { ...current, user: null } : current));
    setVideos([]);
    navigate(null);
  }

  /**
   * Живой ход работы в каталоге — опросом, а не потоком событий.
   *
   * Поток (`subscribeToJob`) подписан на одну запись, и для каталога
   * пришлось бы держать по соединению на карточку. Событий здесь немного,
   * а обновление раз в три секунды человек воспринимает как живое. Опрос
   * идёт, только пока что-то обрабатывается, и только когда каталог открыт:
   * висеть в фоне без дела ему незачем.
   */
  const busy = videos.some((video) => video.state === "processing");
  // Ссылка на свежую функцию: интервал ставится один раз на «занят/не занят»,
  // а не пересоздаётся при каждом обновлении списка.
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (videoId !== null || !busy) return;
    const timer = setInterval(() => void refreshRef.current(), POLL_MS);
    return () => clearInterval(timer);
  }, [busy, videoId]);

  // Возврат к вкладке — повод обновиться сразу: за время отсутствия
  // обработка могла закончиться, и ждать очередного тика незачем.
  useEffect(() => {
    if (videoId !== null) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshRef.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [videoId]);

  // Пока неизвестно, нужен ли вход, не показываем ничего: мелькнувший на
  // мгновение чужой экран хуже, чем пустой.
  if (who === null) return <div className="app" />;

  if (who.auth_required && !who.user) {
    return (
      <div className="app">
        <header className="top">
          <h1>Narezka OS</h1>
          <ThemePicker />
        </header>
        <main id="content">
          <LoginScreen state={who} onEntered={entered} />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <a href="#content" className="sr-only">
        Перейти к содержимому
      </a>
      <header className="top">
        <h1>Narezka OS</h1>
        <ThemePicker />
        {health && (
          <span className="env small dim">
            профиль {health.profile} · {health.device.kind} · {health.device.name}
          </span>
        )}
        {money?.enabled && (
          /* Счёт рядом с ценами: «осталось 40» ничего не значит, пока
             непонятно, на сколько часов записи этого хватит. */
          <span
            className={`badge tnum${money.balance <= 0 ? " bad" : ""}`}
            title={`Разбор записи — ${money.rates.per_video_hour.analysis ?? 0} кредитов за час`}
          >
            {money.balance.toFixed(0)} кредитов
          </span>
        )}
        {who.user && !who.user.local && (
          <span className="account small">
            <span className="dim">{who.user.login}</span>
            <button className="ghost" onClick={() => void leave()}>
              Выйти
            </button>
          </span>
        )}
      </header>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      <main id="content">
        {videoId ? (
          <VideoDetail videoId={videoId} onBack={() => navigate(null)} />
        ) : (
          <ProjectCatalog
            projects={videos}
            onOpen={(id) => navigate(id)}
            onChanged={refresh}
            local={who.user?.local ?? true}
          />
        )}
      </main>
    </div>
  );
}
