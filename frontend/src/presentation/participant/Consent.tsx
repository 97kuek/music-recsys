import { useState } from 'react';

export function Consent({ onAccept, message, disabled, localOnly = false, retentionDays }: {
  onAccept: (code?: string, reuse?: boolean) => void;
  message?: string; disabled: boolean; localOnly?: boolean; retentionDays?: number | null;
}) {
  const [code, setCode] = useState('');
  const [showCode, setShowCode] = useState(false);
  return <section className="screen participation-screen">
    <div className="participation-entry scroll-region">
      <h1 id="step-heading" tabIndex={-1}>はじめに</h1>
      <p className="muted">好きな曲から、次に聴きたい音楽を見つけましょう。</p>
      <p className="participation-summary">右上の「？」にある利用説明・データの取り扱いを確認し、同意のうえ開始してください。{localOnly ? '操作情報はブラウザ内だけで扱います。' : `選択曲や操作履歴・長期的な好みを${retentionDays ? `登録から${retentionDays}日間` : '登録済みの保存期限まで'}保存し、後日のおすすめにも利用します。再訪で保存期限は延長しません。`}</p>
      {localOnly ? <button className="primary" disabled={disabled} onClick={() => onAccept(undefined, false)}>デモを始める</button> : <>
        <p>初回も再訪も、実施者から受け取った同じ参加コードを使ってください。コードが不明な場合は実施者へ確認してください。</p>
        <form onSubmit={event => { event.preventDefault(); if (code.trim() && !disabled) onAccept(code.trim(), true); }}>
          <label htmlFor="participant-code">参加コード</label>
          <div className="code-entry"><input id="participant-code" type={showCode ? 'text' : 'password'} inputMode="numeric" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={64} placeholder="4桁のコード" aria-describedby="code-help" value={code} onChange={event => setCode(event.target.value)} /><button type="button" aria-pressed={showCode} onClick={() => setShowCode(!showCode)}>{showCode ? '隠す' : '表示'}</button></div>
          <small id="code-help">先頭の0も入力してください。以前の長いコードも利用できます。</small>
          <button className="primary" disabled={disabled || !code.trim()}>{disabled ? '確認中…' : '同意して始める・再開する'}</button>
        </form>
      </>}
      {message && <p role="alert">{message}</p>}
    </div>
  </section>;
}
