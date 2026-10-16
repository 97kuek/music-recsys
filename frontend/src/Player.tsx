import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { MediaIcon } from './MediaIcon';
import { api, apiUrl, type EventInput, type Track } from './api';

export type PlayerHandle = { stop: () => Promise<void>; play: (track: Track, request?: string) => Promise<void> };
type Binding = { track: Track; request?: string; playback?: string };
const time = (n: number) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
export const Player = forwardRef<PlayerHandle, {
  emit: (event: EventInput) => Promise<void>; onError: (error: unknown) => void;
  onNext?: () => void; canNext?: boolean; onExpandedChange?: (expanded: boolean) => void;
  onSelect?: () => void; selected?: boolean; onLike?: () => void; liked?: boolean; onSave?: () => void; saved?: boolean; queue?: Track[]; onChoose?: (track: Track) => void;
}>(function Player({ emit, onError, onNext, canNext = true, onExpandedChange, onSelect, selected, onLike, liked, onSave, saved, queue = [], onChoose }, ref) {
  const audio = useRef<HTMLAudioElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const binding = useRef<Binding | null>(null);
  const listened = useRef(0);
  const lastTick = useRef<{wall: number; position: number} | null>(null);
  const [track, setTrack] = useState<Track>();
  const [license, setLicense] = useState<string>();
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const open = () => { dialog.current?.showModal(); onExpandedChange?.(true); };
  const tick = () => {
    const a = audio.current; if (!a) return;
    const next = {wall: performance.now(), position: a.currentTime};
    const prev = lastTick.current;
    if (prev && !a.seeking) {
      const wall = (next.wall - prev.wall) / 1000, media = next.position - prev.position;
      if (wall >= 0 && media >= 0 && media <= wall + .5) listened.current += Math.min(wall, media);
    }
    lastTick.current = a.paused || a.ended || a.seeking ? null : next;
    setPosition(a.currentTime);
  };
  const record = (kind: string) => {
    tick(); const b = binding.current;
    if (!b?.request) return Promise.resolve();
    return emit({ kind, request_id: b.request, item_id: b.track.item_id, playback_id: b.playback, position_seconds: audio.current?.currentTime || 0, listened_seconds: listened.current });
  };
  const stop = async () => {
    tick(); const old = binding.current; binding.current = null;
    if (audio.current && !audio.current.paused) {
      audio.current.pause();
      if (old?.request) await emit({kind:'pause', request_id:old.request, item_id:old.track.item_id, playback_id:old.playback, position_seconds:audio.current.currentTime, listened_seconds:listened.current});
    }
    audio.current?.removeAttribute('src'); audio.current?.load();
    dialog.current?.close(); setTrack(undefined); setPlaying(false); lastTick.current = null;
  };
  const toggle = async () => { const a = audio.current; if (!a?.getAttribute('src')) return; if (a.paused) await a.play(); else a.pause(); };
  useEffect(() => () => { audio.current?.pause(); }, []);
  useImperativeHandle(ref, () => ({stop, play: async (next, request) => {
    await stop();
    const data = await api<{playback_id?:string; url:string; external?:boolean; license_url?:string}>(request ? 'playbacks' : 'previews', 'POST', request ? {request_id:request, item_id:next.item_id} : {item_id:next.item_id});
    listened.current = 0; lastTick.current = null; setPosition(0); setDuration(0); setLoop(false);
    binding.current = {track:next, request, playback:data.playback_id}; setTrack(next); setLicense(data.license_url);
    open();
    if (audio.current) { audio.current.loop = false; audio.current.src = data.external ? data.url : apiUrl(data.url); await audio.current.play(); }
  }}));
  return <>
    <audio ref={audio} preload="none" loop={loop} onTimeUpdate={tick} onLoadedMetadata={() => setDuration(Number.isFinite(audio.current?.duration) ? audio.current!.duration : 0)}
      onSeeking={() => { lastTick.current = null; }} onWaiting={() => { tick(); lastTick.current = null; }}
      onPlaying={() => { setPlaying(true); record('play').catch(onError); }}
      onPause={() => { setPlaying(false); if (binding.current && audio.current?.getAttribute('src') && !audio.current.ended) record('pause').catch(onError); }}
      onEnded={() => { setPlaying(false); record('ended').catch(onError); }} onError={() => { setPlaying(false); record('error').catch(onError); }} />
    {track && <div className="mini-player"><button className="mini-identity" onClick={open} aria-label={`${track.title}のプレイヤーを開く`}><span className="mini-art">♪</span><span><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small></span></button><button className="icon-button" onClick={() => toggle().catch(onError)} aria-label={playing ? '一時停止' : '再生'}><MediaIcon name={playing ? 'pause' : 'play'} /></button></div>}
    <dialog ref={dialog} className="now-playing" aria-label="楽曲プレイヤー" onClose={() => onExpandedChange?.(dialog.current?.open ?? false)}>
      <div className="player-heading"><button className="icon-button" aria-label="一覧に戻る" onClick={() => dialog.current?.close()}><MediaIcon name="down" /></button>{license && <a href={license} target="_blank" rel="noreferrer">ライセンス</a>}</div>
      <div className="playback-layout"><div className="playback-focus">
      <div className="album-art"><div className="album-frame">{track?.artwork ? <img src={track.artwork} alt="" referrerPolicy="no-referrer" /> : <span className="record-art" aria-hidden="true"><i /></span>}</div></div>
      <div className="player-title"><h2>{track?.title}</h2><p>{track?.artists.join(' / ')}</p></div>
      <div className="player-feedback">
        {onSelect && <button aria-pressed={selected} onClick={onSelect}>{selected ? '選択済み' : 'この曲を選択'}</button>}
        {onLike && <button aria-label="いいね" aria-pressed={liked} disabled={liked} onClick={onLike}><MediaIcon name="like" /><span>いいね</span></button>}
        {onSave && <button aria-label="保存" aria-pressed={saved} disabled={saved} onClick={onSave}><MediaIcon name="save" /><span>保存</span></button>}
      </div>
      <div className="timeline"><input aria-label="再生位置" type="range" min="0" max={duration || 0} step="0.1" value={Math.min(position, duration)} disabled={!duration} onChange={e => { tick(); lastTick.current = null; if (audio.current) audio.current.currentTime = Number(e.target.value); setPosition(Number(e.target.value)); }} /><div><span>{time(position)}</span><span>{time(duration)}</span></div></div>
      <div className="transport"><button className="icon-button" aria-label="最初から再生" onClick={() => { tick(); lastTick.current = null; if(audio.current) audio.current.currentTime = 0; setPosition(0); }}><MediaIcon name="previous" /></button><button className="play-pause" aria-label={playing ? '一時停止' : '再生'} onClick={() => toggle().catch(onError)}><MediaIcon name={playing ? 'pause' : 'play'} /></button><button className="icon-button" aria-label="次の曲" disabled={!onNext || !canNext} onClick={onNext}><MediaIcon name="next" /></button><button className="icon-button" aria-label="リピート" aria-pressed={loop} onClick={() => setLoop(v => !v)}><MediaIcon name="repeat" /></button></div>
      </div>{queue.length > 0 && <aside className="playback-queue" aria-label="再生キュー"><h2>再生キュー</h2><div className="scroll-region">{queue.map(item => <button key={item.item_id} className={`queue-track ${item.item_id === track?.item_id ? 'current-song' : ''}`} aria-label={`${item.title}を再生`} aria-current={item.item_id === track?.item_id ? 'true' : undefined} disabled={item.playback_status !== 'available' && item.provider !== 'jamendo'} onClick={() => onChoose?.(item)}>{item.artwork ? <img src={item.artwork} alt="" referrerPolicy="no-referrer" /> : <span className="queue-art" aria-hidden="true">♪</span>}<span><strong>{item.title}</strong><small>{item.artists.join(' / ')}</small></span></button>)}</div></aside>}</div>
    </dialog>
  </>;
});
