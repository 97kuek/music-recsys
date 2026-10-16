import { useEffect, useRef, useState } from 'react';
import { api, type EventInput, type Recommendation, type Status, type Track } from './api';
import { Player, type PlayerHandle } from './Player';
import { Consent } from './Consent';
import { MediaIcon } from './MediaIcon';

type Phase = 'profile' | 'listen' | 'evaluate';
type Profile = { items: string[]; artists: string[] };
type Session = { phase: Phase; period: number; periods: number; finished: boolean; profile: Profile; recommendation: Recommendation | null };

export function Participant({ status }: { status: Status }) {
  const key = `music-profile-v1:${status.catalog_key}`;
  const [profile, setProfile] = useState<Profile>({ items: [], artists: [] });
  const [expanded, setExpanded] = useState(false);
  const [phase, setPhase] = useState<Phase>('profile');
  const [active, setActive] = useState(false);
  const [finished, setFinished] = useState(false);
  const [persist, setPersist] = useState(false);
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
  const queue = useRef(Promise.resolve());
  const timings = useRef(new Map<string, {start: number; response: number}>());
  const seen = useRef(new Set<string>());
  const current = result?.tracks[index];
  const error = (e: unknown) => setMessage(e instanceof Error ? e.message : String(e));
  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setMessage('');
    try { await fn(); } catch (e) { error(e); } finally { setBusy(false); }
  };
  const emit = (event: EventInput): Promise<void> => {
    const next = queue.current.then(async () => { await api('events', 'POST', { ...event, event_id: crypto.randomUUID() }); });
    queue.current = next.catch(error);
    return next;
  };
  const stop = async () => { await player.current?.stop(); await queue.current; };
  const apply = (state: Session) => {
    setActive(true); setFinished(state.finished); setPhase(state.phase);
    setPeriod(state.period); setPeriods(state.periods); setProfile(state.profile);
    setResult(state.recommendation || undefined); setIndex(0); setRating(undefined); setActions([]);
  };
  useEffect(() => {
    try { localStorage.removeItem(key); } catch { /* Storage can be disabled. */ }
    api<Session>('session/state').then(apply).catch(() => {});
  }, [key]);
  useEffect(() => {
    if (!active || phase !== 'profile') return;
    let valid = true;
    const timer = setTimeout(() => api<{ tracks: Track[]; artists: string[]; total: number }>(`catalog?offset=${offset}`).then(data => {
      if (valid) { setTracks(previous => offset === 0 ? data.tracks : [...previous, ...data.tracks]); setTotal(data.total); }
    }).catch(error), 180);
    return () => { valid = false; clearTimeout(timer); };
  }, [offset, phase, active]);
  useEffect(() => {
    if (!active || finished) return;
    const send = (kind: string) => { api('session/progress', 'POST', { phase, kind }, true).catch(() => {}); };
    const visibility = () => send(document.hidden ? 'hidden' : 'visible');
    const pagehide = () => send('hidden');
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pagehide);
    return () => { document.removeEventListener('visibilitychange', visibility); window.removeEventListener('pagehide', pagehide); };
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
    await stop(); await api('session/progress', 'POST', { phase: next }); setPhase(next);
  };
  const recommend = async () => {
    await stop();
    const started = performance.now();
    const reply = await api<Recommendation>('recommendations', 'POST', { goal, top_k: 5 });
    timings.current.set(reply.request_id, {start: started, response: performance.now() - started});
    setResult(reply); setIndex(0); setActions([]); setExpanded(false);
  };
  const start = () => run(async () => {
    const state = await api<Session>('sessions', 'POST', { persist, consent_version: 'local-ui-v2' });
    setActive(true); setFinished(false); setPeriod(state.period); setPeriods(state.periods); setPhase('profile');
  });
  const beginListening = () => run(async () => {
    await api('profile', 'PUT', profile);
    await enter('listen'); await recommend();
  });
  const feedback = (kind: string, track: Track) => run(async () => {
    if (!result) return;
    await emit({ kind, request_id: result.request_id, item_id: track.item_id });
    setActions(a => [...a, `${kind}:${track.item_id}`]);
  });
  const nextTrack = () => run(async () => {
    if (!current || !result || index + 1 >= result.tracks.length) return;
    await emit({ kind: 'skip', request_id: result.request_id, item_id: current.item_id });
    if (index + 1 < result.tracks.length) {
      setIndex(index + 1);
      await player.current?.play(result.tracks[index + 1], result.request_id);
    }
  });
  const finishPeriod = () => run(async () => {
    await stop();
    if (rating && result) await emit({ kind: 'rating', request_id: result.request_id, rating });
    if (period < periods) {
      const state = await api<Session>('session/next', 'POST', { period }); apply(state); await recommend();
    } else { await api('session/finish', 'POST', {}); setFinished(true); }
  });
  const remove = () => run(async () => {
    await stop(); await api('session', 'DELETE'); localStorage.removeItem(key);
    setActive(false); setFinished(false); setResult(undefined); setProfile({ items: [], artists: [] }); setTracks([]); setOffset(0); setPersist(false); setPhase('profile');
    setMessage('データを削除しました');
  });
  const toggle = (field: keyof Profile, value: string) => {
    setProfile(p => ({ ...p, [field]: p[field].includes(value) ? p[field].filter(x => x !== value) : [...p[field], value].slice(0, field === 'items' ? 30 : 10) }));
  };
  const playable = (track: Track) => track.playback_status === 'available' || track.provider === 'jamendo';
  const art = (track: Track, i: number) => track.artwork
    ? <img src={track.artwork} alt="" loading="lazy" referrerPolicy="no-referrer" />
    : <span className={`cover tone${i % 4}`} aria-hidden="true" />;
  return <div className="participant app-shell">
    <header><a className="brand" href="/" aria-label="music-recsys"><img src="/favicon.svg" width="28" height="28" alt="" /> music-recsys</a>{active && !finished && <button className="icon-button" aria-label="終了・削除" title="終了・削除" disabled={busy} onClick={remove}><MediaIcon name="exit" /></button>}</header>
    <main>
      {message && <div className="notice" role="status" aria-live="polite">{message}</div>}
      {!status.ready && <p className="notice">ただいま準備中です。</p>}
      {!active ? <Consent localOnly={status.logging_mode === 'browser-memory'} persist={persist} onPersist={setPersist} onAccept={start} disabled={busy || !status.ready} />
      : finished ? <section className="screen completion"><h1 id="step-heading" tabIndex={-1}>終了しました</h1><button className="primary" onClick={remove} disabled={busy}>データを削除して戻る</button></section>
      : phase === 'profile' ? <section className="screen">
        <div className="section-heading"><h1 id="step-heading" tabIndex={-1}>曲を選択</h1><span className="muted">{profile.items.length}曲選択中</span></div>
        <div className="song-list scroll-region" aria-label="選択する楽曲" tabIndex={0}>
          {tracks.map((track, i) => <article className="song-row" key={track.item_id}>
            <button className="track-open" aria-label={`${track.title}を開く`} disabled={busy || !playable(track)} onClick={() => run(async () => { setPreviewTrack(track); await player.current?.play(track); })}>{art(track, i)}<span className="song-info"><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small>{!playable(track) && <small>試聴できません</small>}</span></button>
            <button className="icon-button select-song" aria-label={`${track.title}を選択`} aria-pressed={profile.items.includes(track.item_id)} onClick={() => toggle('items', track.item_id)}>{profile.items.includes(track.item_id) ? '✓' : '+'}</button>
          </article>)}
          {tracks.length < total && <button className="quiet more-tracks" disabled={busy} onClick={() => setOffset(tracks.length)}>さらに表示</button>}
        </div>
        <div className="screen-actions"><button className="primary" disabled={busy || (!profile.items.length && !profile.artists.length)} onClick={beginListening}>選択して次へ</button></div>
      </section> : phase === 'listen' ? <section className="screen">
        <div className="section-heading"><h1 id="step-heading" tabIndex={-1}>おすすめ</h1>{periods > 1 && <span className="muted">{period} / {periods}</span>}</div>
        {status.experience !== 'experiment' && <div className="request-controls"><label className="sr-only" htmlFor="goal">聴き方</label><select id="goal" value={goal} onChange={e => setGoal(e.target.value)}><option value="auto">おまかせ</option><option value="discover">新しい曲</option><option value="familiar">いつもの好み</option></select><button className="quiet" disabled={busy} onClick={() => run(recommend)}>更新</button></div>}
        <div className="song-list recommendations scroll-region" aria-label="おすすめの楽曲" tabIndex={0}>{result?.tracks.map((track, i) => <article className={`song-row ${i === index ? 'current-song' : ''}`} data-item={track.item_id} key={track.item_id}>
          <button className="track-open" aria-label={`${track.title}を開く`} disabled={busy || !playable(track)} onClick={() => run(async () => { if (result) { setIndex(i); await player.current?.play(track, result.request_id); } })}>{art(track, i)}<span className="song-info"><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small>{!playable(track) && <small>試聴できません</small>}</span></button>
          <div className="song-actions">{['like', 'save'].map((kind, n) => <button className="icon-button" key={kind} aria-label={['いいね', '保存'][n]} aria-pressed={actions.includes(`${kind}:${track.item_id}`)} disabled={busy || actions.includes(`${kind}:${track.item_id}`)} onClick={() => feedback(kind, track)}>{['♡', '＋'][n]}</button>)}</div>
        </article>)}</div>
        <div className="screen-actions">{result ? <button className="primary" disabled={busy} onClick={() => run(() => enter('evaluate'))}>次へ</button> : <button disabled={busy} onClick={() => run(() => enter('profile'))}>曲を選び直す</button>}</div>
      </section> : <section className="screen completion">
        <h1 id="step-heading" tabIndex={-1}>好みに合いましたか？</h1><p className="muted">回答は任意です</p>
        <div className="ratings" role="group" aria-label="満足度">{[1, 2, 3, 4, 5].map(n => <button key={n} aria-label={`満足度 ${n}`} aria-pressed={rating === n} onClick={() => setRating(rating === n ? undefined : n)}>{n}<small>{n === 1 ? '合わない' : n === 5 ? 'よかった' : ''}</small></button>)}</div>
        <div className="screen-actions"><button className="quiet" disabled={busy} onClick={() => run(() => enter('listen'))}>戻る</button><button className="primary" disabled={busy} onClick={finishPeriod}>{period < periods ? '次のおすすめへ' : '終了する'}</button></div>
      </section>}
    </main>
    {active && !finished && phase !== 'evaluate' && <Player ref={player} emit={emit} onError={error} onNext={phase === 'listen' ? nextTrack : undefined} canNext={!!result && index + 1 < result.tracks.length} onExpandedChange={setExpanded}
      queue={phase === 'profile' ? tracks : result?.tracks}
      onChoose={track => run(async () => { if (phase === 'profile') { setPreviewTrack(track); await player.current?.play(track); } else if (result) { setIndex(result.tracks.indexOf(track)); await player.current?.play(track, result.request_id); } })}
      onSelect={phase === 'profile' && previewTrack ? () => toggle('items', previewTrack.item_id) : undefined}
      selected={!!previewTrack && profile.items.includes(previewTrack.item_id)}
      onLike={phase === 'listen' && current ? () => feedback('like', current) : undefined}
      liked={!!current && actions.includes(`like:${current.item_id}`)}
      onSave={phase === 'listen' && current ? () => feedback('save', current) : undefined}
      saved={!!current && actions.includes(`save:${current.item_id}`)} />}
    <footer>{status.logging_mode === 'browser-memory' ? 'デモ音源・架空の曲名／操作ログはブラウザ内のみ' : status.mode === 'demo' ? 'デモ音源・架空の曲名を使用しています' : status.mode === 'jamendo' ? '音源提供：Jamendo' : '試聴可能な音源のみ再生できます'}</footer>
  </div>;
}
