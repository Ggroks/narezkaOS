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
};

export type JobSnapshot = {
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  events: JobEvent[];
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
  videos: () => request<VideoSummary[]>("/api/videos"),
  video: (id: string) => request<VideoDetail>(`/api/videos/${id}`),
  transcript: (id: string) => request<Transcript>(`/api/videos/${id}/transcript`),
  addVideo: (payload: { url?: string; file?: string }) =>
    request<{ video_id: string }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  deleteVideo: (id: string) => request<{ status: string }>(`/api/videos/${id}`, { method: "DELETE" }),
  run: (id: string, payload: { stage?: string; force?: boolean }) =>
    request<{ started: boolean; status: string }>(`/api/videos/${id}/run`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,
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
