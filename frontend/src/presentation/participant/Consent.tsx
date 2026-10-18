import { useStudy } from '../StudyProvider';
import { useEffect, useRef, useState } from 'react';

export function Consent({ onAccept, onContinue, issuedCode, message, disabled, localOnly = false, retentionDays }: {
  onAccept: (code?: string, reuse?: boolean) => void; onContinue: () => void;
  issuedCode?: string; message?: string; disabled: boolean; localOnly?: boolean; retentionDays?: number | null;
}) {
  const service = useStudy();
  const dialog = useRef<HTMLDialogElement>(null);
  const [code, setCode] = useState('');
  const [expires, setExpires] = useState('');
  const [error, setError] = useState('');
  const [copyMessage, setCopyMessage] = useState('');
  useEffect(() => { if (issuedCode && !dialog.current?.open) dialog.current?.showModal(); }, [issuedCode]);
  useEffect(() => {
    setExpires(''); setError('');
    if (!code) return;
    let active = true;
    const timer = setTimeout(() => service.checkCode(code).then(reply => { if (active) setExpires(reply.expires_at); }).catch(() => { if (active) setError('参加コードを確認してください'); }), 450);
    return () => { active=false;clearTimeout(timer); };
  }, [code, service]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(issuedCode!); setCopyMessage('コピーしました'); }
    catch { setCopyMessage('コピーできませんでした。コードを選択して控えてください'); }
  };
  return <section className="screen participation-screen">
    <div className="participation-entry scroll-region">
      <h1 id="step-heading" tabIndex={-1}>はじめに</h1>
      <p className="muted">好きな曲から、次に聴きたい音楽を見つけましょう。</p>
      <p className="participation-summary">右上の「？」にある利用説明・データの取り扱いを確認し、同意のうえ開始してください。{localOnly ? '操作情報はブラウザ内だけで扱います。' : `選択曲や操作履歴・長期的な好みを${retentionDays ? `${retentionDays}日間` : '既存の保存期限まで'}保存し、後日のおすすめにも利用します。`}</p>
      <button className="primary" disabled={disabled || (!localOnly && !retentionDays)} onClick={() => onAccept(undefined, !localOnly)}>{localOnly ? 'デモを始める' : '初めて利用する'}</button>
      {!localOnly && <>
        {!retentionDays && <small>新規参加は保存期間の設定を待っています。参加コードがあれば保存期限内なら再開できます。</small>}
        <form onSubmit={event => { event.preventDefault(); if (expires && !disabled) onAccept(code, true); }}>
          <label>参加コード<input type="password" autoComplete="off" maxLength={64} placeholder="参加コードを入力" value={code} onChange={event => setCode(event.target.value)} /></label>
          {expires && <small>保存期限：{new Date(expires).toLocaleDateString('ja-JP')}</small>}{error && <p role="status">{error}</p>}
          <button disabled={disabled || !expires}>参加コードで再開する</button>
        </form>
      </>}
      {message && <p role="alert">{message}</p>}
    </div>
    {!localOnly && <dialog ref={dialog} className="participation-dialog" aria-labelledby="participation-title" onCancel={event => { if (issuedCode || disabled) event.preventDefault(); }}>
      <h2 id="participation-title">参加コードを控えてください</h2>
        <p>次回も同じ参加者として利用できます。別端末やCookie削除後の再開に使うので、このコードを控えてください。</p>
        <div><p>発行された参加コード</p><output className="issued-code" aria-label="発行された参加コード" tabIndex={0}>{issuedCode}</output></div>
        <button onClick={copy}>参加コードをコピー</button>{copyMessage && <p role="status">{copyMessage}</p>}
        <button className="primary" onClick={() => { dialog.current?.close(); onContinue(); }}>参加コードを控えて進む</button>
    </dialog>}
  </section>;
}
