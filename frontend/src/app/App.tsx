import { useStudy } from '../presentation/StudyProvider';
import { lazy, Suspense, useEffect, useState } from 'react';
import { type Status } from '../domain/contracts';
import { Participant } from '../presentation/participant/Participant';

const Researcher = lazy(() => import('../presentation/researcher/Researcher').then(m => ({ default: m.Researcher })));

export function App() {
  const service = useStudy();
  const [status, setStatus] = useState<Status>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const check = () => service.status().then(value => { if (active) { setStatus(value);setError(''); } }).catch(() => { if (active) { setStatus(undefined); setError('現在接続できません'); } });
    check();const timer=setInterval(check,30000);return () => { active=false;clearInterval(timer); };
  }, []);
  if (!status) return <main className="connection-screen"><img src="/favicon.svg" alt="" width="64" height="64" /><h1>music-recsys</h1><p>{error || '接続を確認しています…'}</p>{error && <><p>API未接続のため、推薦・試聴・ログ収集は利用できません。</p><button onClick={() => window.location.reload()}>接続を再確認</button></>}</main>;
  return status.audience === 'researcher'
    ? <Suspense fallback={<main>準備しています…</main>}><Researcher /></Suspense>
    : <Participant status={status} />;
}
