import { useEffect, useRef, useState } from 'react';

export function Consent({ persist, onPersist, onAccept, disabled, localOnly = false }: {
  persist: boolean; onPersist: (value: boolean) => void; onAccept: () => void; disabled: boolean; localOnly?: boolean;
}) {
  const body = useRef<HTMLDivElement>(null);
  const [read, setRead] = useState(false);
  const check = () => { const el = body.current; if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 8) setRead(true); };
  useEffect(() => { const observer = new ResizeObserver(check); if (body.current) observer.observe(body.current); check(); return () => observer.disconnect(); }, []);
  return <section className="screen consent-screen">
    <h1 id="step-heading" tabIndex={-1}>利用について</h1>
    <div className="scroll-region consent-copy" ref={body} onScroll={check} tabIndex={0} aria-label="利用説明とデータの取り扱い">
      <h2>このアプリについて</h2><p>music-recsysでは、曲を選び、おすすめを試聴できます。現在は開発用の動作確認です。デモでは架空の曲名と合成音源を使用します。</p>
      <h2>記録する内容</h2><p>選択した曲、おすすめの曲と順位、再生・一時停止・スキップ・保存・いいね、試聴時間、任意の評価、操作にかかった時間を扱います。操作の分かりやすさと推薦機能の確認に使用します。</p>
      {localOnly && <p>この公開デモはブラウザ内だけで動作します。行動ログはサーバーへ保存せず、ページを閉じるか再読み込みするとセッションを失います。</p>}
      {!localOnly && <><h2>保存への協力は任意です</h2><p>下のチェックを選択した場合だけ、仮名のセッションIDと操作ログをサーバーへ保存します。氏名やメールアドレスはアプリの行動ログに保存しません。</p><p>選択しなくても利用できます。その場合は動作に必要な情報を一時メモリだけで扱い、行動ログをデータベースへ保存しません。</p>
      <h2>保存期間</h2><p>開発用ログの保持期限は開始から24時間です。稼働中は期限を確認して削除します。サーバー停止中は自動削除が動かず、再起動後の確認時に削除されます。</p>
      <h2>終了・削除</h2><p>画面上部の「終了・削除」から、いつでもセッションと保存したログを削除できます。終了後にも削除できます。初期プロフィールはこの端末へ新たに保存しません。</p>
      </>}<h2>評価への回答</h2><p>おすすめに対する評価は任意です。回答せずに終了できます。再生操作だけから満足度を断定することはありません。</p>
      <h2>開発用の説明です</h2><p>実参加者の研究データ収集を開始する際は、実施者・連絡先・研究目的・保持規程を別途確定します。今回の説明への同意が、将来の研究利用への包括的な同意になることはありません。</p>
      <p className="muted">説明版：local-ui-v2</p>
    </div>
    <div className="screen-actions consent-actions">{!localOnly && <label><input type="checkbox" checked={persist} onChange={e => onPersist(e.target.checked)} /> 操作ログの24時間保存に協力する（任意）</label>}<button className="primary" disabled={disabled || !read} onClick={onAccept}>同意して進む</button>{!read && <small>説明を最後までスクロールしてください</small>}</div>
  </section>;
}
