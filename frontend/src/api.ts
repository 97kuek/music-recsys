export type Track = { item_id: string; title: string; artists: string[]; sample: boolean; artwork?: string | null; provider?: string; license_url?: string | null; playback_status: string };
export type Model = { version: string; algorithm: string; parameters: Record<string, unknown>; als_fold_in: boolean };
export type Metric = { task: string; metric: string; k: number; window_days: number; mean: number | null; day_weight?: unknown };
export type Comparison = { condition: string; accuracy: Metric[]; diversity: unknown; efficiency: unknown; general_binary_ndcg: null };
export type Status = { audience: string; experience: string; ready: boolean; catalog_key: string; mode: string; logging_mode?: string; models: Model[]; unavailable: string[] };
export type Recommendation = { request_id: string; tracks: Track[]; model_version: string; route: string; latency_ms: number; notice: string | null };
export type EventInput = { request_id: string; kind: string; item_id?: string; rating?: number; playback_id?: string; position_seconds?: number; listened_seconds?: number; response_latency_ms?: number; display_latency_ms?: number };

// Jamendo resolves current availability when the user requests a preview.
export const canPreview = (track: Track) => track.playback_status === 'available' || track.provider === 'jamendo';

const base = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '');
if (!/^\/(?!\/)[a-zA-Z0-9/_-]+$/.test(base)) throw new Error('API接続は同一Originのパスを指定してください');
export const apiUrl = (path: string) => `${base}/${path.replace(/^\/api\//, '')}`;

export async function api<T>(path: string, method = 'GET', body?: unknown, keepalive = false): Promise<T> {
  if (import.meta.env.VITE_PUBLIC_DEMO === '1') {
    const { browserDemo } = await import('./browserDemo');
    return await browserDemo(path, method, body) as T;
  }
  const response = await fetch(apiUrl(path), {
    method, keepalive, credentials: 'same-origin', signal: AbortSignal.timeout(path === 'status' ? 5000 : 30000),
    headers: { 'X-Music-App': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `入力を確認してください (${response.status})`);
  return data as T;
}
