/** Клиент API. Тот же слой, что и CLI — см. BAZA.md §33. */

export type StageState = {
  name: string;
  description: string;
  device: string;
  optional: boolean;
  status: "pending" | "done";
  finished_at?: string;
  duration?: number;
};

export type VideoSummary = {
  video_id: string;
  title: string;
  origin: string | null;
  duration_seconds: number | null;
  video: { width?: number; height?: number; fps?: number } | null;
  job_status: string | null;
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
  layout: "single" | "split";
  /** Что вшивать в готовый ролик. Каждый пункт отключается отдельно. */
  subtitles_enabled: boolean;
  loudnorm_enabled: boolean;
  /** Не доверять чату в начале записи: там здороваются, а не реагируют. */
  chat_ignore_start: boolean;
  /** Модель и бэкенд зрения — на видео, чтобы сравнивать их на одном материале. */
  llm_model: string | null;
  detector_backend: string | null;
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

export type DetectorsInfo = { selected: string; backends: DetectorOption[] };

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
  videos: () => request<VideoSummary[]>("/api/videos"),
  video: (id: string) => request<VideoDetail>(`/api/videos/${id}`),
  transcript: (id: string) => request<Transcript>(`/api/videos/${id}/transcript`),
  shorts: (id: string) => request<ShortsIndex>(`/api/videos/${id}/shorts`),
  addVideo: (payload: { url?: string; file?: string }) =>
    request<{ video_id: string }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  deleteVideo: (id: string) => request<{ status: string }>(`/api/videos/${id}`, { method: "DELETE" }),
  run: (id: string, payload: { stage?: string; force?: boolean }) =>
    request<{ started: boolean; status: string }>(`/api/videos/${id}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,
  review: (id: string) => request<Review>(`/api/videos/${id}/review`),
  publish: (id: string) => request<PublishTexts>(`/api/videos/${id}/publish`),
  models: () => request<ModelsInfo>("/api/settings/models"),
  detectors: () => request<DetectorsInfo>("/api/settings/detectors"),
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

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
