import { useEffect, useRef, useState } from 'react';
import { useStudy } from '../StudyProvider';
import { toggleSelection, type Phase, type Profile, type Session, type EventInput, type ListEvaluation, type Recommendation, type Status, type Track } from '../../domain/contracts';
import type { PlayerHandle } from '../player/Player';

export function useParticipantSession(status: Status) {
  const service = useStudy();
  const [profile, setProfile] = useState<Profile>({ items: [], artists: [] });
  const [profileLocked, setProfileLocked] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [phase, setPhase] = useState<Phase>('profile');
  const [recoveryCode, setRecoveryCode] = useState<string>();
  const [pendingRegistration, setPendingRegistration] = useState<Session>();
  const [consentVersion, setConsentVersion] = useState('local-ui-v6');
  const [registered, setRegistered] = useState(false);
  const [active, setActive] = useState(false);
  const [finished, setFinished] = useState(false);
  const [period, setPeriod] = useState(1);
  const [periods, setPeriods] = useState(1);
  const [previewTrack, setPreviewTrack] = useState<Track>();
  const [tracks, setTracks] = useState<Track[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [result, setResult] = useState<Recommendation>();
  const [index, setIndex] = useState(0);
  const [rating, setRating] = useState<number>();
  const [discovery, setDiscovery] = useState<ListEvaluation['discovery_status']>('unanswered');
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [evaluated, setEvaluated] = useState(false);
  const [longTermConsent, setLongTermConsent] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [goal, setGoal] = useState('auto');
  const [actions, setActions] = useState<string[]>([]);
  const player = useRef<PlayerHandle>(null);
  const timings = useRef(new Map<string, {start: number; response: number}>());
  const seen = useRef(new Set<string>());
  const evaluationsShown = useRef(new Set<string>());
  const closedLists = useRef(new Set<string>());
  const current = result?.tracks[index];
  const error = (e: unknown) => setMessage(e instanceof Error ? e.message : String(e));
  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setMessage('');
    try { await fn(); } catch (e) { error(e); } finally { setBusy(false); }
  };
  const emit = async (event: EventInput): Promise<void> => { await service.emit(event); };
  const stop = async () => { await player.current?.stop(); await service.flushEvents(); };
  const apply = (state: Session) => {
    setRegistered(state.registered || false); setActive(true); setFinished(state.finished); setPhase(state.phase);
    setPeriod(state.period); setPeriods(state.periods); setProfile(state.profile);
    setProfileLocked(state.profile_locked || false);
    setResult(state.recommendation || undefined); setIndex(0); setRating(undefined); setActions(state.actions || []);
    setLongTermConsent(state.long_term_consent || false);
    setConsentVersion(state.consent_version || 'local-ui-v6');
    setEvaluated(!!state.list_evaluation);
    setRating(state.list_evaluation?.rating ?? undefined);
    setDiscovery(state.list_evaluation?.discovery_status || 'unanswered');
    setDiscovered(state.list_evaluation?.discovery_item_ids || []);
    if (state.list_evaluation && state.recommendation) closedLists.current.add(state.recommendation.request_id);
    if (state.profile_requires_selection) setMessage('保存された好みが30曲を超えています。「保存した好み」で今後使う曲を選んでください');
  };
  useEffect(() => {
    service.forgetLegacyProfile(status.catalog_key);
    service.state().then(state => {
      apply(state);
      if (state.recommendation && !state.finished && !state.list_evaluation) {
        emit({kind:'observation_gap', request_id:state.recommendation.request_id}).catch(error);
      }
    }).catch(() => {});
  }, [status.catalog_key]);
  useEffect(() => {
    if (!active || phase !== 'profile') return;
    let valid = true;
    const timer = setTimeout(() => service.catalog(undefined, offset).then(data => {
      if (valid) { setTracks(previous => offset === 0 ? data.tracks : [...previous, ...data.tracks]); setTotal(data.total); }
    }).catch(error), 180);
    return () => { valid = false; clearTimeout(timer); };
  }, [offset, phase, active]);
  useEffect(() => {
    if (!active || finished) return;
    let hidden = false;
    const hide = () => {
      const event = player.current?.checkpoint();
      if (event) emit(event).catch(error);
      if (!hidden && result && !closedLists.current.has(result.request_id)) emit({kind:'observation_gap', request_id:result.request_id}).catch(error);
      void service.flushEvents(true);
      if (!hidden) { hidden = true; service.progress(phase, 'hidden', true).catch(() => {}); }
    };
    const visibility = () => {
      if (document.hidden) hide();
      else {
        hidden = false;
        service.flushEvents().catch(error);
        service.progress(phase, 'visible').catch(() => {});
      }
    };
    const retry = setInterval(() => { service.flushEvents().catch(() => {}); }, 5000);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', hide);
    window.addEventListener('online', visibility);
    window.addEventListener('offline', hide);
    return () => {
      clearInterval(retry);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', hide);
      window.removeEventListener('online', visibility);
      window.removeEventListener('offline', hide);
    };
  }, [active, phase, finished, result]);
  useEffect(() => {
    if (phase !== 'evaluate' || !result || evaluated || evaluationsShown.current.has(result.request_id)) return;
    evaluationsShown.current.add(result.request_id);
    emit({kind:'evaluation_shown', request_id:result.request_id}).catch(error);
  }, [phase, result, evaluated]);
  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById('step-heading')?.focus();
  }, [phase, active]);
  useEffect(() => {
    if (!result || phase !== 'listen' || finished || expanded) return;
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const item = (entry.target as HTMLElement).dataset.item;
        const id = `${result.request_id}:${item}`;
        if (entry.isIntersecting && item && !seen.current.has(id)) {
          seen.current.add(id); const timing = timings.current.get(result.request_id); emit({ kind: 'impression', request_id: result.request_id, item_id: item, ...(timing ? {response_latency_ms: timing.response, display_latency_ms: performance.now() - timing.start} : {}) }).catch(error);
        }
      }
    }, { threshold: 0.5 });
    document.querySelectorAll('[data-item]').forEach(el => observer.observe(el));
    return () => observer.disconnect();
  }, [result, phase, index, finished, expanded]);
  const enter = async (next: Phase) => {
    await stop(); await service.progress(next); setPhase(next);
  };
  const recommend = async (initialProfile?: Profile) => {
    await stop();
    let started = performance.now();
    const reply = initialProfile
      ? await service.beginListening(initialProfile, { goal, top_k: 5 }, () => { started = performance.now(); })
      : await service.recommend({ goal, top_k: 5 });
    if (initialProfile) setPhase('listen');
    timings.current.set(reply.request_id, {start: started, response: performance.now() - started});
    setResult(reply); setIndex(0); setActions([]); setExpanded(false);
    setRating(undefined); setDiscovery('unanswered'); setDiscovered([]); setEvaluated(false);
  };
  const start = (code?: string, reuse?: boolean) => run(async () => {
    const state = await service.start({ persist: status.logging_mode !== 'browser-memory', consent_version: reuse === undefined ? consentVersion : 'local-ui-v6', long_term_consent: status.logging_mode !== 'browser-memory' && (reuse ?? longTermConsent), ...(code ? {participant_code:code} : {}) });
    if (state.participant_code) { setRecoveryCode(state.participant_code); setPendingRegistration(state); }
    else apply(state);
  });
  const confirmRegistration = () => { if (pendingRegistration) { apply(pendingRegistration); setPendingRegistration(undefined); } };
  const reset = () => { setActive(false); setFinished(false); setRegistered(false); setRecoveryCode(undefined); setPendingRegistration(undefined); setResult(undefined); setProfile({ items: [], artists: [] }); setTracks([]); setOffset(0); setPhase('profile'); };
  const logout = () => run(async () => { await stop(); await service.logout(); reset(); });
  const rotateCode = () => run(async () => { const reply = await service.rotateCode(); setRecoveryCode(reply.participant_code); setMessage('参加コードを再発行しました。以前のコードは利用できません'); });
  const beginListening = () => run(() => recommend(profile));
  const feedback = (kind: string, track: Track) => run(async () => {
    if (!result) return;
    await emit({ kind, request_id: result.request_id, item_id: track.item_id });
    setActions(a => [...a, `${kind}:${track.item_id}`]);
  });
  const finishPeriod = () => run(async () => {
    await stop();
    if (result && !closedLists.current.has(result.request_id)) {
      await emit({ kind: 'list_evaluation', request_id: result.request_id, ...(rating ? {rating} : {}),
        discovery_status: discovery, discovery_item_ids: discovered, observation_complete: true });
      closedLists.current.add(result.request_id); setEvaluated(true);
    }
    if (refreshPending) {
      await service.progress('listen'); await recommend(); setPhase('listen'); setRefreshPending(false); return;
    }
    if (period < periods) {
      const state = await service.next(period); apply(state); await recommend();
    } else { await service.finish(); setFinished(true); }
  });
  const remove = () => run(async () => {
    // Deletion must not depend on successful delivery of pending telemetry.
    try { await player.current?.stop(); } catch { /* Continue to the owner deletion endpoint. */ }
    await service.remove(); service.forgetLegacyProfile(status.catalog_key);
    reset();
    setMessage('データを削除しました');
  });
  const toggle = (field: keyof Profile, value: string) => {
    if (profileLocked) return;
    setProfile(p => toggleSelection(p, field, value));
  };
  const requestRefresh = () => run(async () => { setRefreshPending(true); await enter('evaluate'); });
  return {
    profile,
    profileLocked,
    setExpanded,
    phase,
    recoveryCode,
    pendingRegistration, confirmRegistration,
    registered,
    active,
    finished,
    period,
    periods,
    previewTrack,
    setPreviewTrack,
    tracks,
    setOffset,
    total,
    result,
    index,
    setIndex,
    rating,
    setRating,
    discovery, setDiscovery, discovered, setDiscovered, evaluated, longTermConsent, requestRefresh, refreshPending,
    busy,
    message,
    goal,
    setGoal,
    actions,
    player,
    current,
    error,
    run,
    emit,
    enter,
    recommend,
    start,
    logout,
    rotateCode,
    beginListening,
    feedback,
    finishPeriod,
    remove,
    toggle,
  };
}
