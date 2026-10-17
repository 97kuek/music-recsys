import type { StudyGateway } from '../application/ports';
import type { CatalogPage, Comparison, Playback, Recommendation, Session, Status } from '../domain/contracts';

type Request = <T>(path: string, method?: string, body?: unknown, keepalive?: boolean) => Promise<T>;
export function createStudyGateway(request: Request, mediaUrl: (path: string) => string): StudyGateway {
  return {
    status: () => request<Status>('status'),
    state: () => request<Session>('session/state'),
    catalog: (query = '', offset = 0) => request<CatalogPage>(`catalog?q=${encodeURIComponent(query)}&offset=${offset}`),
    start: command => request<Session>('sessions', 'POST', command),
    saveProfile: profile => request('profile', 'PUT', profile),
    recommend: command => request<Recommendation>('recommendations', 'POST', command),
    event: event => request('events', 'POST', event),
    progress: (phase, kind = 'enter', keepalive = false) => request('session/progress', 'POST', { phase, kind }, keepalive),
    next: period => request<Session>('session/next', 'POST', { period }),
    finish: () => request('session/finish', 'POST', {}),
    logout: () => request('session/logout', 'POST', {}),
    remove: () => request('session', 'DELETE'),
    rotateCode: () => request<{participant_code:string}>('participants/code', 'POST', {}),
    checkCode: code => request<{expires_at:string}>('participants/check', 'POST', { code }),
    playback: (item, requestId) => request<Playback>(requestId ? 'playbacks' : 'previews', 'POST', requestId ? { request_id: requestId, item_id: item } : { item_id: item }),
    mediaUrl,
    comparison: () => request<Comparison[]>('comparison'),
    exportSession: () => request('session/export'),
  };
}
