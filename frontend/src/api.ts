/** Клиент API. Тот же слой, что и CLI — см. BAZA.md §33. */

/** Кусок работы, который запускается одной кнопкой на своей вкладке. */
export type StageGroup = "analysis" | "shorts" | "long";

export type StageState = {
  name: string;
  description: string;
  device: string;
  optional: boolean;
  /** К какой кнопке относится стадия: разбор, ролики или длинная нарезка. */
  group: StageGroup;
  /** Человеческое название — то же, что в сообщениях сервера. */
  title?: string;
  status: "pending" | "done";
  finished_at?: string;
  duration?: number;
};

/**
 * Состояние записи в каталоге.
 *
 * `started` — работа начата, но роликов ещё нет: это не черновик и не
 * готовое, и сваливать его в одно из двух значит врать в карточке.
 */
export type ProjectState =
  | "draft"
  | "started"
  | "queued"
  | "processing"
  | "ready"
  | "failed";

/** Что идёт прямо сейчас: стадия и, если она умеет считать, сколько сделано. */
export type JobProgress = {
  status: string;
  stage: string | null;
  done: number | null;
  total: number | null;
  note: string | null;
  error: string | null;
};

/**
 * Карточка каталога.
 *
 * В интерфейсе запись называется проектом, в API и в хранилище — видео.
 * Расхождение намеренное: переименовывать сущность в путях, в базе и в
 * тридцати модулях ради слова на экране незачем (см. narezka/core/registry.py).
 */
export type VideoSummary = {
  video_id: string;
  /** Имя для показа: своё, иначе заголовок источника, иначе идентификатор. */
  title: string;
  /** Заголовок из источника — второй строкой, когда имя дал человек. */
  source_title: string | null;
  named: boolean;
  origin: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  video: { width?: number; height?: number; fps?: number } | null;
  /** null — проверка файла ещё не выполнялась, есть ли картинка, неизвестно. */
  has_video: boolean | null;
  state: ProjectState;
  stages_done: number;
  stages_total: number;
  shorts: number;
  /** Момент для кадра-превью; null — кадра не будет (нет записи или это звук). */
  poster_at: number | null;
  job_status: string | null;
  job: JobProgress | null;
  /** Место в очереди: 0 — уже выполняется или ничего не ждёт. */
  queue_position: number;
};

export type Word = { word: string; start: number; end: number; probability: number };

export type Segment = {
  id: number;
  start: number;
  end: number;
  text: string;
  no_speech_prob: number;
  avg_logprob: number;
  words: Word[];
  suspect: boolean;
  suspect_reason?: string;
};

export type Transcript = {
  language: string;
  language_probability: number;
  duration_seconds: number;
  model: string;
  device: string;
  segments: Segment[];
  stats: { segments_total: number; segments_suspect: number; words_usable: number };
};

export type VideoDetail = {
  video_id: string;
  project: string;
  /** Место в очереди: 0 — не ждёт. */
  queue_position: number;
  metadata: Record<string, any>;
  stages: StageState[];
  cost: { stages: Record<string, { runs: number; seconds_total: number }> } | null;
  job: JobSnapshot | null;
};

export type JobEvent = {
  seq: number;
  at: string;
  stage: string;
  event: string;
  outcome?: string;
  duration?: number;
  reason?: string;
  message?: string;
  status?: string;
  /** Ход долгой стадии: сделано из скольких и что именно сейчас идёт. */
  done?: number;
  total?: number;
  note?: string;
};

export type JobSnapshot = {
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  events: JobEvent[];
};

export type ShortFile = {
  index: number;
  file: string;
  start: number;
  end: number;
  duration: number;
  size_bytes: number;
  interest_score?: number | null;
  rank?: number | null;
  explanation?: string | null;
};

export type ShortsIndex = {
  width: number;
  height: number;
  background: "blur" | "solid" | "sharp" | "none";
  /** Откуда взяты клипы: отбор моделью или сырые кандидаты. */
  source?: "selection" | "candidates" | "none";
  framing: (Framing & { content_share: number; lost_share: number; summary: string }) | null;
  files: ShortFile[];
};

export type FramingPreset = "full" | "balanced" | "focus" | "fill" | "custom";

export type Framing = {
  /** single — исходник на подложке; split — вебка сверху, контент снизу. */
  layout: "single" | "split" | "track" | "pip";
  /** Что вшивать в готовый ролик. Каждый пункт отключается отдельно. */
  subtitles_enabled: boolean;
  loudnorm_enabled: boolean;
  /** Не доверять чату в начале записи: там здороваются, а не реагируют. */
  chat_ignore_start: boolean;
  /** Модель и бэкенд зрения — на видео, чтобы сравнивать их на одном материале. */
  llm_model: string | null;
  detector_backend: string | null;
  /** Чем сжимать: cpu — качество, gpu — скорость. */
  encoder: string | null;
  /** Сигналы анализа: каждый отключается отдельно. */
  use_loudness: boolean;
  use_speech_rate: boolean;
  use_chat: boolean;
  use_chat_reactions: boolean;
  /** Теги звука: каждый считается и учитывается отдельно. */
  tag_laughter: boolean;
  tag_music: boolean;
  tag_shout: boolean;
  tag_applause: boolean;
  tag_crowd: boolean;
  /** Слежение за лицом: цепкость рамки и мёртвая зона. */
  track_smoothing: number;
  track_dead_zone: number;
  /** Сплит: доля высоты под вебку и приближение лица (меньше — ближе). */
  split_top_share: number;
  face_zoom: number;
  face_vertical: number;
  preset: FramingPreset;
  side_crop: number;
  anchor: "center" | "left" | "right";
  background: "blur" | "color";
  blur_sigma: number;
  color: string;
};

export type PresetPreview = {
  preset: FramingPreset;
  label: string;
  side_crop: number;
  content_share: number;
  full_bleed: boolean;
  summary: string;
};

export type FramingState = {
  current: Framing;
  /** Найдена ли вебка наложением — без неё раскладка «сплит» невозможна. */
  split_available?: boolean;
  /** Настройки заданы вручную для этого видео, а не взяты из конфига. */
  custom: boolean;
  source: { width: number; height: number };
  output: { width: number; height: number };
  plan: { content_share: number; lost_share: number; full_bleed: boolean; summary: string };
  presets: PresetPreview[];
};

export type Verdict = "accept" | "reject";

export type ReviewClip = {
  index: number;
  start: number;
  end: number;
  duration: number;
  peak_at: number;
  provisional_score: number;
  signals: { loudness_z?: number; rms_db?: number; words_per_second?: number };
  text: string;
  verdict: Verdict | null;
  decided_at?: string;
  /** Границы, предложенные автоматикой, — до правок человеком. */
  original?: { start: number; end: number };
  edited: boolean;
  /** Ниже — от стадии llm_select; отсутствует, если она не выполнялась. */
  selected: boolean;
  interest_score?: number | null;
  rank?: number | null;
  explanation?: string;
  /** null означает «не измерено», а не «ноль» — см. §54. */
  factors?: Record<string, number | null>;
  penalties?: Record<string, number | null>;
};

export type PublishEntry = {
  index: number;
  clip_id: string | null;
  start: number;
  end: number;
  title: string;
  title_variants: { title: string; problems: string[] }[];
  description: string;
  hashtags: string[];
  /** Описание вместе с хэштегами — то, что копируют в поле публикации. */
  ready: string;
};

export type PublishTexts = {
  video_id: string;
  models: string[];
  clips: PublishEntry[];
  stats: { clips: number; written: number; titles_rejected: number };
};

export type ReviewStats = {
  total: number;
  accepted: number;
  rejected: number;
  undecided: number;
  edited: number;
  /** Сколько клипов оценила модель. Ноль — стадия llm_select не выполнялась. */
  scored: number;
};

export type Review = { video_id: string; clips: ReviewClip[]; stats: ReviewStats };

export type Measurement = {
  id: number;
  measured_at: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  retention: number | null;
  ctr: number | null;
  note: string | null;
};

export type PublishedClip = {
  clip_id: string;
  clip_index: number;
  title: string | null;
  interest_score: number | null;
  /** Вектор признаков на момент публикации — неизменяемый (§63). */
  factors_snapshot: Record<string, number | null>;
  score_schema_version: number | null;
  prompt_version: number | null;
  model: string | null;
  human_verdict: string | null;
  bounds_shift_start: number | null;
  bounds_shift_end: number | null;
  platform: string | null;
  url: string | null;
  published_at: string | null;
  views: number | null;
  measured_at: string | null;
  history: Measurement[];
};

export type FeedbackReport = {
  clips: number;
  published: number;
  measured: number;
  correlations: {
    metric: string;
    sample: number;
    reliable: boolean;
    min_sample: number;
    factors: Record<string, { correlation: number | null; sample: number }>;
  };
  verdicts: {
    accepted: number;
    rejected: number;
    mean_accepted: number | null;
    mean_rejected: number | null;
    gap: number | null;
    reliable: boolean;
  };
  bounds: {
    sample: number;
    mean_start_shift: number | null;
    mean_end_shift: number | null;
    reliable: boolean;
  };
};

export type Performance = { clips: PublishedClip[]; report: FeedbackReport };

export type ModelOption = {
  id: string;
  context: number;
  free: boolean;
  structured: boolean;
};

export type ModelsInfo = {
  provider: string;
  selected: string | null;
  has_key?: boolean;
  models: ModelOption[];
  error?: string;
};

export type DetectorOption = {
  name: string;
  label: string;
  license: string;
  /** Реализован ли вообще. Нет — выбрать нельзя, и это видно. */
  implemented: boolean;
  available: boolean;
  note: string;
};

export type EncoderOption = {
  name: string;
  label: string;
  /** Плюсы и минусы показываются оба — выбор не должен быть вслепую. */
  pros: string;
  cons: string;
  available: boolean;
  note: string;
};

export type EncodersInfo = { selected: string; encoders: EncoderOption[] };

export type DetectorsInfo = { selected: string; backends: DetectorOption[] };

export type CompilationFile = {
  file: string;
  /** story — связный эпизод; best — подборка лучших моментов. */
  kind: "story" | "best";
  title: string;
  summary: string;
  duration: number;
  pieces: number;
  size_bytes: number;
  /** Готовая строка оглавления для описания под видео. */
  chapters_text?: string;
};

export type CompilationsInfo = { files: CompilationFile[]; reason?: string };

export type EpisodeInfo = {
  start: number;
  end: number;
  duration: number;
  title: string;
  summary: string;
  /** Есть ли у отрезка начало, развитие и завершение. */
  coherence: number;
};

export type EpisodesInfo = {
  episodes: EpisodeInfo[];
  /** Номера выбранных эпизодов; null — самый цельный из длинных. */
  selected?: number[] | null;
  story: boolean;
  best: boolean;
  target_minutes?: number;
  reason?: string;
};

/**
 * Кто вошёл и нужен ли вход.
 *
 * `auth_required: false` — местная работа на своей машине: вход выключен,
 * и интерфейс ведёт себя ровно как раньше.
 */
export type Whoami = {
  auth_required: boolean;
  allow_signup: boolean;
  user: { login: string; workspace: string; is_admin: boolean; local: boolean } | null;
};

/** Счёт и цены. Цены рядом с балансом: «осталось 40» ничего не значит,
 *  пока непонятно, на сколько часов записи этого хватит. */
export type Billing = {
  enabled: boolean;
  balance: number;
  rates: { version: number; per_video_hour: Record<string, number>; minimum: number };
  history: {
    at: string;
    kind: string;
    amount: number;
    video_id: string | null;
    work_groups: string | null;
    video_hours: number | null;
    note: string | null;
  }[];
};

/** Оформление субтитров: набор плюс правки поверх него. */
export type SubtitleStyle = {
  name: string;
  font: string;
  font_size: number;
  outline: number;
  bold: boolean;
  position: "bottom" | "middle" | "top";
  max_words_per_line: number;
  max_chars_per_line: number;
  max_lines: number;
  /** Цвета в виде #RRGGBB — в самом ASS порядок байтов обратный. */
  primary_hex: string;
  highlight_hex: string;
  outline_hex: string;
};

export type SubtitlesState = { preset: string; custom: boolean; style: SubtitleStyle };

export type SubtitlePreset = {
  name: string;
  title: string;
  note: string;
  style: Record<string, unknown>;
  colours: { primary: string; highlight: string; outline_colour: string };
};

export type SubtitleOptions = {
  presets: SubtitlePreset[];
  fonts: { name: string; note: string }[];
  positions: { name: string; title: string }[];
  /** Готовые степени приближения лица в сплите. */
  face_zoom: { name: string; title: string; note: string; zoom: number }[];
};

export type HealthCheck = { name: string; ok: boolean; detail: string; critical: boolean };
export type Health = {
  ok: boolean;
  profile: string;
  device: { kind: string; name: string };
  checks: HealthCheck[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* тело может быть не JSON — оставляем статус */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  me: () => request<Whoami>("/api/auth/me"),
  billing: () => request<Billing>("/api/billing"),
  login: (login: string, password: string) =>
    request<Whoami>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    }),
  signup: (login: string, password: string, invite: string) =>
    request<Whoami>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ login, password, invite }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  videos: () => request<VideoSummary[]>("/api/videos"),
  video: (id: string) => request<VideoDetail>(`/api/videos/${id}`),
  transcript: (id: string) => request<Transcript>(`/api/videos/${id}/transcript`),
  shorts: (id: string) => request<ShortsIndex>(`/api/videos/${id}/shorts`),
  addVideo: (payload: { url?: string; file?: string; title?: string; rights?: string }) =>
    request<{ video_id: string; created: boolean; title: string; placement: string }>("/api/videos", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** Пустое название возвращает заголовок из источника. */
  renameVideo: (id: string, title: string) =>
    request<VideoSummary>(`/api/videos/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteVideo: (id: string) => request<{ status: string }>(`/api/videos/${id}`, { method: "DELETE" }),
  run: (id: string, payload: { stage?: string; group?: StageGroup; force?: boolean }) =>
    request<{ started: boolean; status: string; queued: boolean; position: number }>(
      `/api/videos/${id}/run`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,
  /**
   * Момент отдельным куском — для обзора вместо целой записи.
   *
   * Замер объяснил, зачем: пятнадцать секунд просмотра на записи в 4.58 ч
   * вытянули 26.7 ГБ, вчетверо больше самого файла. Браузер на каждой
   * перемотке просит новый кусок и бросает предыдущий, а раздаёт их сервер
   * по-настоящему.
   *
   * Границы в адресе не ради сервера, а ради браузера: после правки границ
   * кусок другой, и без них показался бы прежний, из кэша.
   */
  reviewMediaUrl: (id: string, index: number, start: number, end: number) =>
    `/api/videos/${id}/review/${index}/media?v=${start.toFixed(1)}-${end.toFixed(1)}`,
  review: (id: string) => request<Review>(`/api/videos/${id}/review`),
  publish: (id: string) => request<PublishTexts>(`/api/videos/${id}/publish`),
  models: () => request<ModelsInfo>("/api/settings/models"),
  detectors: () => request<DetectorsInfo>("/api/settings/detectors"),
  episodes: (id: string) => request<EpisodesInfo>(`/api/videos/${id}/episodes`),
  /** Что собирать в длинную нарезку. Выбор хранится при записи. */
  setCompilation: (
    id: string,
    payload: { story: boolean; best: boolean; episodes: number[] | null; target_minutes: number },
  ) =>
    request<EpisodesInfo>(`/api/videos/${id}/compilation`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  /** Пересобрать один готовый ролик, не трогая остальные. */
  rerenderShort: (id: string, index: number) =>
    request<{ started: boolean; status: string; position: number }>(
      `/api/videos/${id}/shorts/${index}/render`,
      { method: "POST" },
    ),
  stop: (id: string) => request<{ stopping: boolean }>(`/api/videos/${id}/stop`, { method: "POST" }),
  compilations: (id: string) => request<CompilationsInfo>(`/api/videos/${id}/compilations`),
  compilationUrl: (id: string, file: string) =>
    `/api/videos/${id}/compilations/${encodeURIComponent(file)}/media`,
  encoders: () => request<EncodersInfo>("/api/settings/encoders"),
  performance: (id: string) => request<Performance>(`/api/videos/${id}/performance`),
  markPublished: (id: string, payload: { index: number; platform: string; url?: string }) =>
    request<Performance>(`/api/videos/${id}/performance/publish`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addMetrics: (
    id: string,
    payload: {
      clip_id: string;
      views?: number;
      likes?: number;
      comments?: number;
      shares?: number;
      retention?: number;
      ctr?: number;
    },
  ) =>
    request<Performance>(`/api/videos/${id}/performance/metrics`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  setReview: (id: string, index: number, payload: { verdict?: Verdict; start?: number; end?: number }) =>
    request<Review>(`/api/videos/${id}/review/${index}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  clearReview: (id: string, index: number) =>
    request<Review>(`/api/videos/${id}/review/${index}`, { method: "DELETE" }),
  /** Кадр исходника в заданный момент — миниатюра карточки. */
  frameUrl: (id: string, at: number, height = 240) =>
    `/api/videos/${id}/frame?at=${at.toFixed(2)}&height=${height}`,
  subtitleOptions: () => request<SubtitleOptions>("/api/settings/subtitles"),
  subtitles: (id: string) => request<SubtitlesState>(`/api/videos/${id}/subtitles`),
  setSubtitles: (
    id: string,
    payload: {
      preset: string;
      font?: string;
      font_size?: number;
      primary?: string;
      highlight?: string;
      outline_colour?: string;
      outline?: number;
      bold?: boolean;
      position?: string;
      max_words_per_line?: number;
      max_chars_per_line?: number;
      max_lines?: number;
    },
  ) =>
    request<SubtitlesState>(`/api/videos/${id}/subtitles`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  resetSubtitles: (id: string) =>
    request<SubtitlesState>(`/api/videos/${id}/subtitles`, { method: "DELETE" }),
  /** Кадр с вшитыми субтитрами. `v` заставляет браузер перезапросить его
   *  после правки — адрес иначе тот же, и показался бы прежний. */
  subtitlePreviewUrl: (id: string, version: string) =>
    `/api/videos/${id}/subtitles/preview?v=${encodeURIComponent(version)}`,
  framing: (id: string) => request<FramingState>(`/api/videos/${id}/framing`),
  setFraming: (id: string, payload: Framing) =>
    request<FramingState>(`/api/videos/${id}/framing`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  resetFraming: (id: string) =>
    request<FramingState>(`/api/videos/${id}/framing`, { method: "DELETE" }),
  /** Кадр в готовой рамке — предпросмотр до полного рендера. */
  framingPreviewUrl: (id: string, framing: Framing) => {
    const query = new URLSearchParams(
      Object.entries(framing).map(([key, value]) => [key, String(value)]),
    );
    return `/api/videos/${id}/framing/preview?${query}`;
  },
};

/**
 * Подписка на события обработки.
 * §69: поток с сервера, а не опрос — иначе система выглядит зависшей.
 */
export function subscribeToJob(
  videoId: string,
  since: number,
  onEvent: (event: JobEvent) => void,
): () => void {
  const source = new EventSource(`/api/videos/${videoId}/events?since=${since}`);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as JobEvent);
    } catch {
      /* пульс соединения — не событие */
    }
  };
  source.onerror = () => source.close();
  return () => source.close();
}

/**
 * Дата добавления обычными словами.
 *
 * «Сегодня» и «вчера» вместо числа: в каталоге, где работа идёт каждый день,
 * дата нужна, чтобы отличить свежее от старого, а не чтобы её прочесть.
 */
/**
 * Загрузка записи файлом.
 *
 * Через XMLHttpRequest, а не fetch: у fetch нет хода отправки, а запись
 * на шесть гигабайт без полосы выглядит зависшей. Тело — сам файл, без
 * multipart: браузер отдаёт его потоком, сервер потоком пишет на диск,
 * и ни у кого он не лежит в памяти целиком.
 */
export function uploadVideo(
  file: File,
  title: string | undefined,
  onProgress: (share: number) => void,
  rights?: string,
): Promise<{ video_id: string; title: string }> {
  return new Promise((resolve, reject) => {
    const query = new URLSearchParams({ name: file.name });
    if (title) query.set("title", title);
    if (rights) query.set("rights", rights);

    const request = new XMLHttpRequest();
    request.open("POST", `/api/videos/upload?${query}`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        resolve(JSON.parse(request.responseText));
        return;
      }
      let detail = `${request.status}`;
      try {
        detail = JSON.parse(request.responseText).detail ?? detail;
      } catch {
        /* тело может быть не JSON — оставляем код */
      }
      reject(new Error(detail));
    };
    request.onerror = () => reject(new Error("не удалось отправить файл"));
    request.send(file);
  });
}

/**
 * Во сколько обойдётся работа — та же формула, что на сервере.
 *
 * Считается по всем кускам, которые могут выполниться, то есть как если бы
 * кэша не было: списание выйдет меньше или столько же. Показывать меньше
 * названного можно, больше — нельзя, поэтому граница верхняя.
 */
export function estimateCredits(
  billing: Billing | null,
  groups: string[],
  seconds: number | null | undefined,
): number | null {
  if (!billing?.enabled || !seconds) return null;
  const hours = seconds / 3600;
  const total = groups.reduce(
    (sum, group) => sum + (billing.rates.per_video_hour[group] ?? 0) * hours,
    0,
  );
  return total > 0 ? Math.max(total, billing.rates.minimum) : 0;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";

  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(new Date()) - midnight(date)) / 86_400_000);
  if (days === 0) return "сегодня";
  if (days === 1) return "вчера";
  if (days < 7) return `${days} дн. назад`;

  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: sameYear ? undefined : "numeric",
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
