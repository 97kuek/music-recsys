import { useEffect, useRef, useState } from 'react';
import { useStudy } from '../StudyProvider';
import { toggleSelection, type Phase, type Profile, type Session, type EventInput, type Recommendation, type Status, type Track } from '../../domain/contracts';
import type { PlayerHandle } from '../player/Player';

export function useParticipantSession(status: Status) {
  const service = useStudy();
  const [profile, setProfile] = useState<Profile>({ items: [], artists: [] });
  const [expanded, setExpanded] = useState(false);
  const [phase, setPhase] = useState<Phase>('profile');
  const [recoveryCode, setRecoveryCode] = useState<string>();
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
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [goal, setGoal] = useState('auto');
  const [actions, setActions] = useState<string[]>([]);
  const player = useRef<PlayerHandle>(null);
  const timings = useRef(new Map<string, {start: number; response: number}>());
  const seen = useRef(new Set<string>());
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
    setResult(state.recommendation || undefined); setIndex(0); setRating(undefined); setActions(state.actions || []);
  };
  useEffect(() => {
    service.forgetLegacyProfile(status.catalog_key);
    service.state().then(apply).catch(() => {});
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
    return () => {
      clearInterval(retry);
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', hide);
      window.removeEventListener('online', visibility);
    };
  }, [active, phase, finished]);
  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById('step-heading')?.focus();
  }, [phase]);
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
    const started = performance.now();
    const reply = initialProfile
      ? await service.beginListening(initialProfile, { goal, top_k: 5 })
      : await service.recommend({ goal, top_k: 5 });
    if (initialProfile) setPhase('listen');
    timings.current.set(reply.request_id, {start: started, response: performance.now() - started});
    setResult(reply); setIndex(0); setActions([]); setExpanded(false);
  };
  const start = (code?: string) => run(async () => {
    const state = await service.start({ persist: status.logging_mode !== 'browser-memory', consent_version: 'local-ui-v4', ...(code ? {participant_code:code} : {}) });
    apply(state);
    if (state.participant_code) { setRecoveryCode(state.participant_code); setMessage('参加コードを発行しました。右上の「？」から控えてください'); }
  });
  const reset = () => { setActive(false); setFinished(false); setRegistered(false); setRecoveryCode(undefined); setResult(undefined); setProfile({ items: [], artists: [] }); setTracks([]); setOffset(0); setPhase('profile'); };
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
    if (rating && result) await emit({ kind: 'rating', request_id: result.request_id, rating });
    if (period < periods) {
      const state = await service.next(period); apply(state); await recommend();
    } else { await service.finish(); setFinished(true); }
  });
  const remove = () => run(async () => {
    await stop(); await service.remove(); service.forgetLegacyProfile(status.catalog_key);
    reset();
    setMessage('データを削除しました');
  });
  const toggle = (field: keyof Profile, value: string) => {
    setProfile(p => toggleSelection(p, field, value));
  };
  return {
    profile,
    setExpanded,
    phase,
    recoveryCode,
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
