export type Descriptive = {
  mean: number | null; median: number | null; participants: number; answered_participants: number;
  answers: number; missing: number; response_rate: number | null;
  distribution?: Record<string, number>;
  by_participant: {participant_id: string; value: number | null; answers: number; missing: number; session_ids: string[]}[];
};
type Total = {total: number | null; measured_subtotal: number | null; measured: number; missing: number};
type Behavior = Record<'like_rate' | 'play_start_rate' | 'engaged_listen_rate' | 'track_switch_rate' | 'early_skip_rate', Descriptive> & {
  engaged_sensitivity: Record<string, Descriptive>;
  discovery: {success_tracks: number | null; success_lists: number | null; answered: number; offered: number; success_list_rate: number | null; response_rate: number | null};
};
export type OnlineCondition = {
  condition_id: string; initial: Descriptive; final: Descriptive; stages: Record<string, Descriptive>;
  improvement: Descriptive; unadjusted: number; not_started: number; abandoned: number; unfinished: number;
  behavior: Behavior; behavior_by_stage: Record<string, Behavior>;
  latency: Record<string, Total>; usage: Record<string, Total>;
};
export type OnlineOutcome = {
  participant_id: string; session_id: string; condition_id: string; position: number | null;
  session_status: string; condition_status: string; adjustment_count: number | null;
  initial_satisfaction: number | null; final_satisfaction: number | null;
  stages: Record<string, {status: string; satisfaction: number | null; request_id: string | null}>;
  improvement: number | null; improvement_status: string; request_ids: string[];
};
export type OnlineExperiment = {
  protocol_id: string; plan_sha256: string | null; plan: {design?: string; conditions: {condition_id: string}[]} | null;
  config_sha256: string | null;
  participants: number; session_count: number; conditions: OnlineCondition[]; outcomes: OnlineOutcome[];
  assignments: {participant_id: string; slot: number; order: number[]; assigned_at: string}[];
  sessions: {session_id: string; participant_id: string; status: string; order: number[]; mode: string}[];
  lists: Record<string, unknown>[];
  paired_differences: {participant_id: string; stage: string; condition_a: string; condition_b: string; a: number | null; b: number | null; difference_b_minus_a: number | null}[];
  diagnostics: Record<string, number>;
  order_effects: (Descriptive & {condition_id: string; position: number})[];
};
export type OnlineAnalysis = {format: string; generated_at: string; experiments: OnlineExperiment[]};
