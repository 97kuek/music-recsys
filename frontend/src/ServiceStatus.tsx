import { useEffect, useState } from 'react';

type Availability = { state: 'online' | 'offline' | 'unconfigured' | 'restricted'; next_open_at: string | null; checked_at: string; mode?: string };
export function ServiceStatus({ localOnly = false }: { localOnly?: boolean }) {
  const [status, setStatus] = useState<Availability>();
  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch('/availability', {cache:'no-store', credentials:'same-origin', signal:AbortSignal.timeout(5000)});
        if (!response.ok) throw new Error('unavailable');
        const data = await response.json();
        if (!['online','offline','unconfigured','restricted'].includes(data.state)) throw new Error('invalid availability');
        if (active) setStatus(data);
      } catch { if (active) setStatus({state:'offline',next_open_at:null,checked_at:new Date().toISOString()}); }
    };
    check(); const timer = setInterval(check,30000);
    const visible = () => { if (!document.hidden) check(); };
    document.addEventListener('visibilitychange',visible);
    return () => { active=false;clearInterval(timer);document.removeEventListener('visibilitychange',visible); };
  }, []);
  const text = !status ? '稼働状況を確認中' : status.state === 'online' ? 'サーバー稼働中' : status.state === 'unconfigured' ? '実曲サーバー未接続' : status.state === 'restricted' ? '認証が必要です' : '現在サーバーを利用できません';
  const opening = status?.next_open_at && Date.parse(status.next_open_at) > Date.now() ? new Date(status.next_open_at).toLocaleString('ja-JP', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}) : null;
  return <div className="service-status" role="status" aria-live="polite" data-state={status?.state || 'checking'}><span className="status-light" aria-hidden="true" /><span>{localOnly && 'デモ稼働中 · '}{text}{status && status.state !== 'online' && <small>{opening ? `次回予定：${opening}` : '次回予定：未定'}</small>}</span></div>;
}
