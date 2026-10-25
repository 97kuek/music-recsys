export type Track = { item_id: string; title: string; artists: string[]; sample: boolean; artwork?: string | null; provider?: string; license_url?: string | null; playback_status: string };
export type Model = { version: string; algorithm: string; parameters: Record<string, unknown>; als_fold_in: boolean };
export type Metric = { task: string; metric: string; k: number; window_days: number; mean: number | null; day_weight?: unknown };
export type Comparison = { condition: string; accuracy: Metric[]; diversity: unknown; efficiency: unknown; general_binary_ndcg: null };
export type Status = { audience: string; experience: string; ready: boolean; catalog_key: string; mode: string; logging_mode?: string; retention_days?: number | null; models: Model[]; unavailable: string[] };
export type Recommendation = { request_id: string; tracks: Track[]; model_version: string; route: string; latency_ms: number; notice: string | null };
export type Movement = 'next' | 'previous' | 'direct';
export type ListEvaluation = { rating?: number; discovery_status: 'unanswered' | 'none' | 'selected'; discovery_item_ids: string[]; observation_complete: boolean };
export type EventInput = { request_id: string; kind: string; item_id?: string; rating?: number; playback_id?: string; position_seconds?: number; listened_seconds?: number; response_latency_ms?: number; display_latency_ms?: number; event_version?: 1 | 2; movement?: Movement; target_item_id?: string; was_playing?: boolean; discovery_status?: ListEvaluation['discovery_status']; discovery_item_ids?: string[]; observation_complete?: boolean };

export const canPreview = (track: Track) => track.playback_status === 'available';


export type Phase = 'profile' | 'listen' | 'evaluate';
export type Profile = { items: string[]; artists: string[] };
export type Session = { phase: Phase; period: number; periods: number; finished: boolean; profile: Profile; recommendation: Recommendation | null; actions?: string[]; registered?: boolean; consent_version?: string; list_evaluation?: ListEvaluation | null; profile_locked?: boolean };
export type CatalogPage = { tracks: Track[]; artists: string[]; total: number };
export type Playback = { playback_id?: string; url: string; external?: boolean; license_url?: string };
export type StartCommand = { persist: boolean; allow_text?: boolean; consent_version?: string };
export type RecommendCommand = { version?: string; goal: string; top_k: number; text?: string };
export function toggleSelection(profile: Profile, field: keyof Profile, value: string): Profile {
  return { ...profile, [field]: profile[field].includes(value) ? profile[field].filter(x => x !== value) : [...profile[field], value].slice(0, field === 'items' ? 30 : 10) };
}
