import { lazy, Suspense, useEffect, useState } from 'react';
import { api, type Status } from './api';
import { Participant } from './Participant';

const Researcher = lazy(() => import('./Researcher').then(m => ({ default: m.Researcher })));

export function App() {
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState('');
  useEffect(() => { api<Status>('status').then(setStatus).catch(e => setError(String(e))); }, []);
  if (!status) return <main className="connection-screen"><img src="/favicon.svg" alt="" width="64" height="64" /><h1>music-recsys</h1><p role="status">{error || '接続を確認しています…'}</p>{error && <><p>API未接続のため、推薦・試聴・ログ収集は利用できません。</p><button onClick={() => window.location.reload()}>接続を再確認</button></>}</main>;
  return status.audience === 'researcher'
    ? <Suspense fallback={<main>準備しています…</main>}><Researcher /></Suspense>
    : <Participant status={status} />;
}
