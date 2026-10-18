import { useStudy } from '../StudyProvider';
import { useEffect, useRef, useState } from 'react';

export function Consent({ onAccept, disabled, localOnly = false, retentionDays }: {
  onAccept: (code?: string, reuse?: boolean) => void; disabled: boolean; localOnly?: boolean; retentionDays?: number | null;
}) {
  const service = useStudy();
  const body = useRef<HTMLDivElement>(null);
  const [read, setRead] = useState(false);
  const [returning, setReturning] = useState(false);
  const [code, setCode] = useState('');
  const [expires, setExpires] = useState('');
  const [error, setError] = useState('');
  const [reuse, setReuse] = useState(false);
  const check = () => { const el = body.current; if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 8) setRead(true); };
  useEffect(() => { const observer = new ResizeObserver(check); if (body.current) observer.observe(body.current); check(); return () => observer.disconnect(); }, []);
  useEffect(() => {
    setExpires(''); setError('');
    if (!returning || !code) return;
    let active = true;
    const timer = setTimeout(() => service.checkCode(code).then(reply => { if (active) setExpires(reply.expires_at); }).catch(() => { if (active) setError('参加コードを確認してください'); }), 450);
    return () => { active=false;clearTimeout(timer); };
  }, [code, returning]);
  return <section className="screen consent-screen">
    <h1 id="step-heading" tabIndex={-1}>はじめに</h1>
    <div className="scroll-region consent-copy" ref={body} onScroll={check} tabIndex={0} aria-label="利用説明とデータの取り扱い">
      <p>曲を選んで、おすすめを試聴できます。現在は開発用です。</p>
      {localOnly ? <p>このデモの情報はブラウザ内だけで扱い、再読み込みで消えます。サーバーへ行動ログを保存しません。</p>
      : <><p>選択曲・推薦結果・再生やいいね・試聴時間・任意評価を、機能の確認に使います。同意して開始すると、仮名IDとこれらの情報を{expires ? `${new Date(expires).toLocaleDateString('ja-JP')}まで` : retentionDays ? `${retentionDays}日間` : '管理者が設定した期間'}保存します。氏名・メールは含めません。</p><p>初回に参加コードを発行します。同じブラウザでは自動で、別端末では参加コードで再開できます。コードを他の人へ共有しないでください。</p><p>退室してもデータは残ります。「？」の削除操作で全データと参加コードを削除できます。期限切れのデータは、停止中なら次回起動時に削除します。</p></>}
      <p>評価への回答は任意です。この同意には、将来の研究利用を含みません。</p>
      {!localOnly && <><p>追加で同意した場合、選択・Like・試聴・曲移動・評価の履歴を同じ期限まで保持し、後日のおすすめのために好みの曲と既存アーティスト情報を表示・再利用します。一時的な要望を恒久的な好みにしません。「保存した好み」で確認・修正でき、全参加データの削除で撤回できます。期限は利用のたびに延長しません。</p><label><input type="checkbox" checked={reuse} onChange={e => setReuse(e.target.checked)} />長期嗜好の保存・後日の再利用にも同意する（任意）</label><p>自由文の保存や外部APIへの送信には同意していません。今回、自由文・外部API・Agentは利用しません。</p></>}
      <small className="muted">説明版：local-ui-v5</small>
    </div>
    {!localOnly && <div className="returning-participant"><button className="quiet" aria-expanded={returning} onClick={() => setReturning(v => !v)}>{returning ? '初めて利用する' : '参加コードで再開する'}</button>{returning && <><input type="password" aria-label="参加コード" autoComplete="off" maxLength={64} value={code} onChange={e => setCode(e.target.value)} placeholder="参加コード" />{expires && <small>保存期限：{new Date(expires).toLocaleDateString('ja-JP')}</small>}{error && <small role="status">{error}</small>}</>}</div>}
    <div className="screen-actions consent-actions"><button className="primary" disabled={disabled || !read || (!localOnly && (returning ? !expires : !retentionDays))} onClick={() => onAccept(returning ? code : undefined, reuse)}>同意して進む</button>{!read && <small>説明を最後までスクロールしてください</small>}{!localOnly && !retentionDays && !returning && <small>管理者による保存期間の設定を待っています</small>}</div>
  </section>;
}
