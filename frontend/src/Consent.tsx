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
      <p>曲を選んで、おすすめを試聴できます。現在は開発用です。デモには架空の曲名と合成音源を使います。</p>
      {localOnly ? <p>選択・推薦・操作の情報はブラウザ内だけで扱い、再読み込みで消えます。行動ログはサーバーへ保存しません。</p>
      : <><p>選択曲・推薦結果・再生やいいねの操作・試聴時間・処理時間・任意評価を、機能の確認に使います。ログ保存への協力は任意です。</p><p>チェックした場合だけ、仮名IDとログを開始から24時間保存します。氏名・メールは行動ログに含めません。未選択なら一時メモリだけで扱います。</p><p>終了・削除アイコンから、いつでもこの端末のログを削除できます。期限切れは稼働中に削除し、停止中に期限を過ぎた分は次回起動時に削除します。</p></>}
      <p>評価への回答は任意です。この同意には、将来の研究利用を含みません。</p>
      <small className="muted">説明版：local-ui-v3</small>
    </div>
    <div className="screen-actions consent-actions">{!localOnly && <label><input type="checkbox" checked={persist} onChange={e => onPersist(e.target.checked)} /> 操作ログを24時間保存する（任意）</label>}<button className="primary" disabled={disabled || !read} onClick={onAccept}>同意して進む</button>{!read && <small>説明を最後までスクロールしてください</small>}</div>
  </section>;
}
