import { ListEvaluation } from './ListEvaluation';
import { SavedPreferences } from './SavedPreferences';
import { useParticipantSession } from './useParticipantSession';
import { canPreview, type Status } from '../../domain/contracts';
import { Player } from '../player/Player';
import { Consent } from './Consent';
import { MediaIcon } from '../shared/MediaIcon';
import { GoalPicker } from '../shared/GoalPicker';
import { Artwork } from '../shared/Artwork';
import { Information } from '../shared/Information';

export function Participant({ status }: { status: Status }) {
  const {
    profile,
    profileLocked,
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
  } = useParticipantSession(status);
  return <div className="participant app-shell">
    <header><a className="brand" href="/" aria-label="music-recsys"><img src="/favicon.svg" width="28" height="28" alt="" /> music-recsys</a><div className="header-actions">{active && registered && longTermConsent && <SavedPreferences onDelete={remove} />}<Information status={status} code={recoveryCode} onCode={registered ? rotateCode : undefined} onDelete={active ? remove : undefined} />{active && <button className="icon-button" aria-label="退室する" title="退室する" disabled={busy} onClick={logout}><MediaIcon name="exit" /></button>}</div></header>
    <main>
      {message && <div className="notice" role="status" aria-live="polite">{message}</div>}
      {!status.ready && <p className="notice">ただいま準備中です。</p>}
      {!active ? <Consent localOnly={status.logging_mode === 'browser-memory'} retentionDays={status.retention_days} onAccept={start} message={message} disabled={busy || !status.ready} />
      : finished ? <section className="screen completion"><h1 id="step-heading" tabIndex={-1}>終了しました</h1><p className="muted">この画面を閉じても記録は保存されます</p><button className="primary" onClick={() => start()} disabled={busy}>もう一度</button><button className="quiet" onClick={logout} disabled={busy}>退室する</button></section>
      : phase === 'profile' ? <section className="screen">
        <div className="section-heading"><h1 id="step-heading" tabIndex={-1}>曲を選択</h1><span className="muted">{profile.items.length}曲選択中</span></div>
        {profileLocked && <p className="muted">この比較では開始時の好みを使います。そのまま次へ進めます。</p>}
        <div className="song-list scroll-region" aria-label="選択する楽曲" tabIndex={0}>
          {tracks.map(track => <article className="song-row" key={track.item_id}>
            <button className="track-open" aria-label={`${track.title}を再生`} disabled={busy || !canPreview(track)} onClick={() => run(async () => { setPreviewTrack(track); await player.current?.play(track); })}><Artwork track={track} /><span className="song-info"><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small>{!canPreview(track) && <small>試聴できません</small>}</span></button>
            <button className="icon-button select-song" disabled={profileLocked} aria-label={`${track.title}を選択`} aria-pressed={profile.items.includes(track.item_id)} onClick={() => toggle('items', track.item_id)}>{profile.items.includes(track.item_id) ? '✓' : '+'}</button>
          </article>)}
          {tracks.length < total && <button className="quiet more-tracks" disabled={busy} onClick={() => setOffset(tracks.length)}>さらに表示</button>}
        </div>
        <div className="screen-actions"><button className="primary" disabled={busy || (!profile.items.length && !profile.artists.length)} onClick={beginListening} aria-label="選択して次へ" title="次へ"><MediaIcon name="forward" /></button></div>
      </section> : phase === 'listen' ? <section className="screen">
        <div className="section-heading"><h1 id="step-heading" tabIndex={-1}>おすすめ</h1>{periods > 1 && <span className="muted">{period} / {periods}</span>}</div>
        {status.experience !== 'experiment' && <div className="request-controls"><GoalPicker value={goal} onChange={setGoal} disabled={busy} /><button className="icon-button" aria-label="おすすめを更新" title="更新" disabled={busy} onClick={requestRefresh}><MediaIcon name="refresh" /></button></div>}
        <div className="song-list recommendations scroll-region" aria-label="おすすめの楽曲" tabIndex={0}>{result?.tracks.map((track, i) => <article className={`song-row ${i === index ? 'current-song' : ''}`} data-item={track.item_id} key={track.item_id}>
          <button className="track-open" aria-label={`${track.title}を再生`} disabled={busy || !canPreview(track)} onClick={() => run(async () => { if (result) { setIndex(i); await player.current?.play(track, result.request_id); } })}><Artwork track={track} /><span className="song-info"><strong>{track.title}</strong><small>{track.artists.join(' / ')}</small>{!canPreview(track) && <small>試聴できません</small>}</span></button>
          <div className="song-actions"><button className="icon-button" aria-label="いいね" aria-pressed={actions.includes(`like:${track.item_id}`)} disabled={busy || actions.includes(`like:${track.item_id}`)} onClick={() => feedback('like', track)}><MediaIcon name="like" /></button></div>
        </article>)}</div>
        <div className="screen-actions">{result ? <button className="primary" disabled={busy} onClick={() => run(() => enter('evaluate'))} aria-label="おすすめを評価する" title="評価へ">おすすめを評価する<MediaIcon name="forward" /></button> : <button disabled={busy} onClick={() => run(recommend)}>おすすめを再取得</button>}</div>
      </section> : <section className="screen completion">
        <h1 id="step-heading" tabIndex={-1}>おすすめの評価</h1>
        <ListEvaluation rating={rating} setRating={setRating} tracks={result?.tracks.filter(track => actions.includes(`like:${track.item_id}`)) || []}
          discovery={discovery} setDiscovery={setDiscovery} discovered={discovered} setDiscovered={setDiscovered} disabled={evaluated || busy} />
        <p className="muted evaluation-hint" role="status">{evaluated ? 'このリストの回答は送信済みです' : rating || discovery !== 'unanswered' ? '回答は次へ進むと送信されます' : '回答せずに進むこともできます'}</p>
        <div className="screen-actions evaluation-actions"><button className="quiet" disabled={busy || evaluated} onClick={() => run(() => enter('listen'))} aria-label="試聴に戻る" title="戻る"><MediaIcon name="back" />試聴に戻る</button><button className="primary" disabled={busy} onClick={finishPeriod} aria-label={refreshPending || period < periods ? '次のおすすめへ' : '終了する'} title={period < periods ? '次へ' : '終了する'}>{busy ? '送信中…' : refreshPending || period < periods ? '次のおすすめへ' : '終了する'}<MediaIcon name={refreshPending || period < periods ? 'forward' : 'check'} /></button></div>
      </section>}
    </main>
    {active && !finished && phase !== 'evaluate' && <Player ref={player} emit={emit} onError={error} onExpandedChange={setExpanded}
      queue={phase === 'profile' ? tracks : result?.tracks}
      onChoose={(track, movement) => run(async () => { if (phase === 'profile') { setPreviewTrack(track); await player.current?.play(track); } else if (result) { setIndex(result.tracks.indexOf(track)); await player.current?.play(track, result.request_id, movement); } })}
      onSelect={phase === 'profile' && previewTrack && !profileLocked ? () => toggle('items', previewTrack.item_id) : undefined}
      selected={!!previewTrack && profile.items.includes(previewTrack.item_id)}
      onLike={phase === 'listen' && current ? () => feedback('like', current) : undefined}
      liked={!!current && actions.includes(`like:${current.item_id}`)}
 />}

  </div>;
}
