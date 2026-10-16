import { useEffect, useRef, useState } from 'react';
import { api, canPreview, type EventInput, type Recommendation, type Status, type Track } from './api';
import { Comparison } from './Comparison';
import { Player, type PlayerHandle } from './Player';

function Cover({ track, index }: { track: Track; index: number }) {
  return <div className={`cover tone${index % 4}`} aria-hidden="true"><span>♫</span><small>{track.sample ? 'DEMO ART' : 'NO ARTWORK'}</small></div>;
}

export function Researcher() {
  const [status, setStatus] = useState<Status>();
  const [catalog, setCatalog] = useState<Track[]>([]);
  const [artists, setArtists] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [chosenArtists, setChosenArtists] = useState<string[]>([]);
  const [persist, setPersist] = useState(false);
  const [textConsent, setTextConsent] = useState(false);
  const [started, setStarted] = useState(false);
  const [finished, setFinished] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [version, setVersion] = useState('');
  const [goal, setGoal] = useState('auto');
  const [topK, setTopK] = useState(10);
  const [note, setNote] = useState('');
  const [result, setResult] = useState<Recommendation>();
  const [elapsed, setElapsed] = useState<number>();
  const [rating, setRating] = useState<number>();
  const [log, setLog] = useState<unknown>();
  const [actions, setActions] = useState<string[]>([]);
  const player = useRef<PlayerHandle>(null);
  const queue = useRef(Promise.resolve());
  const error = (e: unknown) => setMessage(e instanceof Error ? e.message : String(e));
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setMessage('');
    try { await fn(); } catch (e) { error(e); } finally { setBusy(false); }
  };
  const emit = (event: EventInput): Promise<void> => {
    const next = queue.current.then(async () => { await api('events', 'POST', { ...event, event_id: crypto.randomUUID() }); });
    queue.current = next.catch(error);
    return next;
  };
  useEffect(() => { api<Status>('status').then(setStatus).catch(error); }, []);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => api<{ tracks: Track[]; artists: string[]; total: number }>(`catalog?q=${encodeURIComponent(query)}`).then(data => {
      if (active) { setCatalog(data.tracks); setArtists(data.artists); setTotal(data.total); }
    }).catch(e => { if (active) error(e); }), 180);
    return () => { active = false; clearTimeout(timer); };
  }, [query]);
  const profile = () => api('profile', 'PUT', { items: selected, artists: chosenArtists });
  const toggle = (id: string, values: string[], setter: (v: string[]) => void, limit: number) => {
    if (values.includes(id)) setter(values.filter(x => x !== id));
    else if (values.length < limit) setter([...values, id]);
    else setMessage(`選択は${limit}件までです`);
  };
  const stop = async () => { await player.current?.stop(); await queue.current; };
  const start = () => run(async () => {
    await stop(); await api('sessions', 'POST', { persist, allow_text: textConsent && persist });
    await profile(); setStarted(true); setFinished(false); setResult(undefined); setRating(undefined); setLog(undefined); setActions([]);
    setMessage('準備できました。比較する推薦方法を選んでください。');
  });
  const recommend = () => run(async () => {
    const begin = performance.now();
    await stop(); await profile();
    const data = await api<Recommendation>('recommendations', 'POST', { version, goal, top_k: topK, text: note });
    setResult(data); setActions([]);
    requestAnimationFrame(() => requestAnimationFrame(() => setElapsed(performance.now() - begin)));
  });
  // 実際に画面内に入った推薦行だけを1回ずつ記録する。
  useEffect(() => {
    if (!result || finished) return;
    const seen = new Set<string>();
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const item = (entry.target as HTMLElement).dataset.item;
        if (entry.isIntersecting && item && !seen.has(item)) {
          seen.add(item); emit({ kind: 'impression', request_id: result.request_id, item_id: item }).catch(error);
        }
      }
    }, { threshold: 0.5 });
    document.querySelectorAll('[data-item]').forEach(row => observer.observe(row));
    return () => observer.disconnect();
  }, [result, finished]);
  const play = (track: Track) => run(async () => {
    if (!result || finished) return;
    await player.current?.play(track, result.request_id);
  });
  const action = (kind: string, item: string) => run(async () => {
    if (!result) return;
    await emit({ kind, request_id: result.request_id, item_id: item }); setActions(a => [...a, `${kind}:${item}`]);
  });
  const end = () => run(async () => {
    await stop();
    if (rating && result) await emit({ kind: 'rating', request_id: result.request_id, rating });
    await api('session/finish', 'POST', {}); setFinished(true); setMessage('セッションを終了しました。データの確認・削除は引き続き行えます。');
  });
  const remove = () => run(async () => {
    await stop(); await api('session', 'DELETE'); setStarted(false); setFinished(false); setResult(undefined); setLog(undefined); setMessage('このセッションのデータを削除しました');
  });
  const model = status?.models.find(m => m.version === version);
  return <>
    <aside className="sidebar"><a className="brand" href="#">◒ <span>music-recsys<small>MUSIC RECSYS / LOCAL LAB</small></span></a><p className="eyebrow">YOUR LISTENING SPACE</p><nav><a href="#profile">01　好みを選ぶ</a><a href="#discover">02　曲を見つける</a><a href="#finish">03　振り返り</a></nav><div className="side-note">● ローカル開発用<br />気になる曲を選んで、<br />次の音に出会いましょう。</div><button className="quiet danger" disabled={busy} onClick={remove}>セッションのデータを削除</button></aside>
    <main><header><span>LISTENING LAB <b> / MVP</b></span><span className="badge">{status?.mode === 'demo' ? 'DEMO / 合成データ' : status ? 'Music4All / 実データ' : '接続を確認中'}</span></header>
      <div className="notice">{status?.mode === 'demo' ? '架空の楽曲・プロフィールと独自合成音源で操作を確認するデモです。Music4Allの実験ではありません。' : 'Music4Allの保存済み実モデルを使用します。曲名・アーティスト・音源は未接続です。'}</div>
      <div role="status" aria-live="polite" id="message">{message}</div>
      <section className="hero"><p className="eyebrow">A LITTLE CURIOSITY, A NEW SOUND.</p><h1>いまの好みから、<br /><em>次の一曲へ。</em></h1><p>好きな音を少し教えてください。<br />あとは聴きながら、あなたのペースで。</p><div className="hero-art" aria-hidden="true"><i /><b>PLAY<br />YOUR<br />CURIOSITY.</b></div></section>
      <section id="profile"><div className="section-title"><div><p className="eyebrow">01 / YOUR TASTE</p><h2>まずは、気になる音を。</h2></div><span>{selected.length}曲・{chosenArtists.length}組を選択中</span></div><p className="muted">選ばなくても始められます。初期選択は再生履歴とは別に扱います。</p>
        <label className="search">⌕ <input type="search" aria-label="楽曲検索" placeholder="楽曲名・アーティスト・IDで探す" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="chips">{artists.map(a => <button key={a} aria-pressed={chosenArtists.includes(a)} onClick={() => toggle(a, chosenArtists, setChosenArtists, 10)}>{a}</button>)}</div>
        <div className="catalog">{catalog.map((track, i) => <button className="track-choice" key={track.item_id} aria-pressed={selected.includes(track.item_id)} onClick={() => toggle(track.item_id, selected, setSelected, 30)}><Cover track={track} index={i} /><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small></button>)}</div>
        {catalog.length < total && <button className="quiet" disabled={busy} onClick={() => run(async () => { const data = await api<{ tracks: Track[] }>(`catalog?q=${encodeURIComponent(query)}&offset=${catalog.length}`); setCatalog(c => [...c, ...data.tracks]); })}>もっと見る</button>}
        <div className="start-panel"><div><label><input type="checkbox" checked={persist} onChange={e => setPersist(e.target.checked)} /> 開発用ログをこのサーバーに24時間保存することに同意する</label><small>任意。未選択なら一時メモリのみ。実被験者のデータ収集は未開始です。</small><label><input type="checkbox" disabled={!persist} checked={textConsent && persist} onChange={e => setTextConsent(e.target.checked)} /> 任意入力の文章も保存する</label><small>新しく開始すると、このブラウザの前のセッションデータを削除します。</small></div><button className="primary" disabled={busy || !status} onClick={start}>この好みで始める ↗</button></div>
      </section>
      <section id="discover"><div className="section-title"><div><p className="eyebrow">02 / FIND YOUR NEXT</p><h2>今日は、どんな聴き方？</h2></div><span className="badge">採用設定は未確定</span></div>
        <div className="goals" role="group" aria-label="推薦の目的">{[['discover', '✧ 新しい曲を発見'], ['familiar', '↻ いつもの好みに'], ['auto', '◈ おまかせ']].map(([key, label]) => <button key={key} className={goal === key ? 'active' : ''} aria-pressed={goal === key} onClick={() => setGoal(key)}>{label}</button>)}</div>
        <label htmlFor="note" className="muted">ひとことメモ（任意・現在は推薦に反映されません）</label><textarea id="note" maxLength={500} value={note} onChange={e => setNote(e.target.value)} placeholder="例えば、ゆったり過ごす午後に。" />
        <div className="request-controls"><label>比較する推薦方法<select value={version} onChange={e => setVersion(e.target.value)}><option value="">推薦方法を選択</option>{status?.models.map(m => <option key={m.version} value={m.version}>{m.version}</option>)}</select></label><label>曲数<select value={topK} onChange={e => setTopK(Number(e.target.value))}>{[5, 10, 20].map(k => <option key={k} value={k}>{k}曲</option>)}</select></label><button className="primary" disabled={!started || finished || !version || busy} onClick={recommend}>おすすめを聴く ↗</button></div>
        {model && <p className="muted">{model.algorithm} / {JSON.stringify(model.parameters)}{model.als_fold_in ? ' / 新規ユーザーのfold-in有効' : ''}</p>}
        {!!status?.unavailable.length && <p className="muted">実モデル未準備：{status.unavailable.join(' / ')}。自動的な再学習・デモへの切替は行いません。</p>}
        <p className="muted">{result ? `${result.model_version} · 経路 ${result.route} · 推論 ${result.latency_ms.toFixed(1)} ms · 要求開始〜描画 ${elapsed?.toFixed(1) ?? '計測中'} ms` : '開始して推薦方法を選ぶと、おすすめを表示できます。'}</p>{result?.notice && <p>{result.notice}</p>}
        <div className="results">{result?.tracks.map((track, i) => <article className="result" data-item={track.item_id} key={track.item_id}><span className="rank">{String(i + 1).padStart(2, '0')}</span><Cover track={track} index={i} /><div className="identity"><strong>{track.title}</strong><small>{track.artists.join(' / ')} · {track.playback_status === 'available' ? (track.sample ? 'デモ試聴可能' : '試聴可能') : '音源未確認・再生不可'}</small></div><div className="actions"><button disabled={busy || finished || !canPreview(track)} onClick={() => play(track)} aria-label={`${track.title}を試聴`}>▶ 試聴</button>{[['like', '♡ いいね'], ['save', '＋ 保存'], ['skip', '↠ スキップ']].map(([kind, label]) => <button key={kind} disabled={busy || finished || actions.includes(`${kind}:${track.item_id}`)} onClick={() => action(kind, track.item_id)}>{label}</button>)}</div></article>)}</div>
      </section>
      <Comparison />
      <section id="finish"><p className="eyebrow">03 / ONE LAST THOUGHT</p><h2>今日のおすすめは、どうでしたか？</h2><p className="muted">回答は任意です。最後にひとつだけ、気が向いたら。</p><div className="ratings" role="group" aria-label="満足度">{[1, 2, 3, 4, 5].map(n => <button key={n} disabled={!result || finished} aria-pressed={rating === n} onClick={() => setRating(rating === n ? undefined : n)}>{n}{n === 1 && <small>合わなかった</small>}{n === 5 && <small>よかった</small>}</button>)}</div>
        <button className="secondary" disabled={!started || finished || busy} onClick={end}>{rating ? '回答して終了' : '回答せずに終了'}</button><button className="quiet" disabled={!started || busy} onClick={() => run(async () => setLog(await api('session/export')))}>自分のログを確認</button>{log !== undefined && <pre>{JSON.stringify(log, null, 2)}</pre>}
      </section><footer>music-recsys · Local development preview</footer>
    </main>
    <Player ref={player} emit={emit} onError={error} queue={result?.tracks} onChoose={play} />
  </>;
}
