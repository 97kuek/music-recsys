import { useRef, useState } from 'react';
import { useStudy } from '../StudyProvider';
import type { Preferences, Profile } from '../../domain/contracts';
import { toggleSelection } from '../../domain/contracts';

export function SavedPreferences({ onDelete }: { onDelete: () => void }) {
  const service = useStudy();
  const dialog = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<Preferences>();
  const [profile, setProfile] = useState<Profile>({items:[], artists:[]});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true); setMessage('');
    try {
      const data = await service.preferences(); setView(data);
      // No arbitrary ordering/weights when more than the existing 30 seed limit is available.
      const union = [...new Set([...data.user_settings.items, ...data.inferred_tracks.map(t => t.item_id)])];
      setProfile({...data.user_settings, items: union.length <= 30 ? union : data.user_settings.items});
      dialog.current?.showModal();
    } catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const options = view ? [...new Map([...view.user_tracks, ...view.inferred_tracks, ...view.excluded_tracks].map(t => [t.item_id,t])).values()] : [];
  return <><button className="quiet preferences-button" aria-label="保存した好み" disabled={busy} onClick={load}><span className="preferences-label">保存した好み</span><span className="preferences-short" aria-hidden="true">好み</span></button>{message && !dialog.current?.open && <small role="status">{message}</small>}
    <dialog ref={dialog} className="information" aria-label="保存した好み"><div className="section-heading"><h2>保存した好み</h2><button onClick={() => dialog.current?.close()}>閉じる</button></div>
      <div className="scroll-region saved-preferences">
        <p>変更は次のセッションから使います。比較中の好みは変わりません</p>
        <p>好意を示した曲の集合から表示しています。ジャンル・ムードや行動の重み付けは推定しません</p>
        <p>保存期限：{view && new Date(view.retain_until).toLocaleDateString('ja-JP')}・履歴 {view?.history.length ?? 0}セッション</p>
        <fieldset disabled={busy}><legend>今後使う好きな曲（最大30曲）</legend>{options.map(track => <label key={track.item_id}><input type="checkbox" checked={profile.items.includes(track.item_id)} onChange={() => setProfile(p => toggleSelection(p, 'items', track.item_id))} />{track.title || track.item_id}
          <small>{view?.user_settings.items.includes(track.item_id) ? '自分で設定' : view?.excluded_items.includes(track.item_id) ? '除外済み' : 'Like履歴から抽出'}</small>
          {track.evidence && <details><summary>根拠を確認</summary>{track.evidence.map(e => <small key={e.event_id}>セッション {e.session_id} / 要求 {e.request_id} / イベント {e.event_id}</small>)}</details>}
        </label>)}</fieldset>
        <fieldset disabled={busy}><legend>好きなアーティスト</legend>{[...new Set([...profile.artists, ...(view?.inferred_artists.map(a => a.artist) || [])])].map(artist => <label key={artist}><input type="checkbox" checked={profile.artists.includes(artist)} onChange={() => setProfile(p => toggleSelection(p, 'artists', artist))} />{artist}<small>{view?.user_settings.artists.includes(artist) ? '自分で設定' : 'Likeした曲のメタデータ・未確定の候補'}</small></label>)}</fieldset>
        {message && <p role="status">{message}</p>}
        <button disabled={busy} onClick={async () => {
          setBusy(true);
          try {
            const excluded = [...new Set([...(view?.excluded_items || []), ...options.map(t => t.item_id)])].filter(id => !profile.items.includes(id));
            setView(await service.savePreferences({...profile, excluded_items:excluded})); setMessage('次のセッション用に保存しました');
          } catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
          finally { setBusy(false); }
        }}>変更を保存</button>
        <p>履歴とプロフィールの削除・同意の撤回は全参加データの削除で行えます</p>
        <button disabled={busy} onClick={() => { dialog.current?.close(); onDelete(); }}>プロフィールと全参加データを削除</button>
      </div>
    </dialog></>;
}
