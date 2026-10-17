import { lazy, Suspense, useEffect, useState } from 'react';
import { api, type Status } from './api';
import { Participant } from './Participant';

const Researcher = lazy(() => import('./Researcher').then(m => ({ default: m.Researcher })));

export function App() {
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const check = () => api<Status>('status').then(value => { if (active) { setStatus(value);setError(''); } }).catch(() => { if (active) setError('現在接続できません'); });
    check();const timer=setInterval(check,30000);return () => { active=false;clearInterval(timer); };
  }, []);
  if (!status) return <main className="connection-screen"><img src="/favicon.svg" alt="" width="64" height="64" /><h1>music-recsys</h1><p>{error || '接続を確認しています…'}</p>{error && <><p>API未接続のため、推薦・試聴・ログ収集は利用できません。</p><button onClick={() => window.location.reload()}>接続を再確認</button></>}</main>;
  return status.audience === 'researcher'
    ? <Suspense fallback={<main>準備しています…</main>}><Researcher /></Suspense>
    : <Participant status={status} />;
}
