import type { ListEvaluation as Answer, Track } from '../../domain/contracts';

export function ListEvaluation({ rating, setRating, tracks, discovery, setDiscovery, discovered, setDiscovered, disabled }: {
  rating?: number; setRating: (value?: number) => void; tracks: Track[];
  discovery: Answer['discovery_status']; setDiscovery: (value: Answer['discovery_status']) => void;
  discovered: string[]; setDiscovered: (items: string[]) => void; disabled: boolean;
}) {
  return <div className="scroll-region list-evaluation">
    <h2>おすすめ全体は好みに合いましたか？</h2><p className="muted">回答は任意です。選んだ数字をもう一度押すと解除できます。</p>
    <div className="ratings" role="group" aria-label="リスト全体の満足度">{[1,2,3,4,5].map(n => <button key={n} disabled={disabled} aria-label={`満足度 ${n}`} aria-describedby={`rating-label-${n}`} aria-pressed={rating === n} onClick={() => setRating(rating === n ? undefined : n)}>{n}<small id={`rating-label-${n}`}>{['合わない', 'あまり合わない', 'どちらでもない', 'やや合う', 'よく合う'][n - 1]}</small></button>)}</div>
    {tracks.length > 0 && <fieldset disabled={disabled}><legend>いいねした曲のうち、今回初めて知った曲は？</legend><small>複数選択できます・回答は任意です</small>
      {tracks.map(track => <label key={track.item_id}><input type="checkbox" checked={discovered.includes(track.item_id)} onChange={() => {
        const next = discovered.includes(track.item_id) ? discovered.filter(id => id !== track.item_id) : [...discovered, track.item_id];
        setDiscovered(next); setDiscovery(next.length ? 'selected' : 'unanswered');
      }} />{track.title} <small>{track.artists.join(' / ')}</small></label>)}
      <label><input type="checkbox" checked={discovery === 'none'} onChange={() => { setDiscovered([]); setDiscovery(discovery === 'none' ? 'unanswered' : 'none'); }} />該当なし</label>
      <small>{discovery === 'unanswered' ? '未回答' : discovery === 'none' ? '該当なしと回答' : `${discovered.length}曲選択`}</small>
    </fieldset>}
    {tracks.length === 0 && <p className="muted">いいねした曲がないため、新しく知った曲の質問はありません。</p>}
  </div>;
}
