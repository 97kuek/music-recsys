export function Consent({ onAccept, message, disabled, localOnly = false, retentionDays }: {
  onAccept: () => void;
  message?: string; disabled: boolean; localOnly?: boolean; retentionDays?: number | null;
}) {
  return <section className="screen participation-screen">
    <div className="participation-entry scroll-region">
      <h1 id="step-heading" tabIndex={-1}>はじめに</h1>
      <p className="muted">好きな曲から、次に聴きたい音楽を見つけましょう。</p>
      <p className="participation-summary">右上の「？」にある利用説明・データの取り扱いを確認し、同意のうえ開始してください。{localOnly ? '操作情報はブラウザ内だけで扱います。' : `選択曲や操作履歴・任意評価を${retentionDays ? `初回の利用開始から${retentionDays}日間` : '登録済みの保存期限まで'}保存し、機能の確認に使います。再訪で保存期限は延長しません。`}</p>
      <button className="primary" disabled={disabled} onClick={() => onAccept()}>{disabled ? '確認中…' : localOnly ? 'デモを始める' : '同意して始める'}</button>
      {message && <p role="alert">{message}</p>}
    </div>
  </section>;
}
