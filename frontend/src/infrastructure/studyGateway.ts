import type { StudyGateway } from '../application/ports';
import type { OnlineAnalysis } from '../domain/onlineAnalysis';
import type { CatalogPage, Comparison, Playback, Preferences, Recommendation, Session, Status } from '../domain/contracts';

type Request = <T>(path: string, method?: string, body?: unknown, keepalive?: boolean) => Promise<T>;
export function createStudyGateway(request: Request, mediaUrl: (path: string) => string): StudyGateway {
  return {
    status: () => request<Status>('status'),
    state: () => request<Session>('session/state'),
    catalog: (query = '', offset = 0) => request<CatalogPage>(`catalog?q=${encodeURIComponent(query)}&offset=${offset}`),
    start: command => request<Session>('sessions', 'POST', command),
    saveProfile: profile => request('profile', 'PUT', profile),
    preferences: () => request<Preferences>('profile/long-term'),
    savePreferences: profile => request<Preferences>('profile/long-term', 'PUT', profile),
    recommend: command => request<Recommendation>('recommendations', 'POST', command),
    event: (event, keepalive = false) => request('events', 'POST', event, keepalive),
    progress: (phase, kind = 'enter', keepalive = false) => request('session/progress', 'POST', { phase, kind }, keepalive),
    next: period => request<Session>('session/next', 'POST', { period }),
    finish: () => request('session/finish', 'POST', {}),
    logout: () => request('session/logout', 'POST', {}),
    remove: () => request('session', 'DELETE'),
    rotateCode: () => request<{participant_code:string}>('participants/code', 'POST', {}),
    playback: (item, requestId) => request<Playback>(requestId ? 'playbacks' : 'previews', 'POST', requestId ? { request_id: requestId, item_id: item } : { item_id: item }),
    mediaUrl,
    comparison: () => request<Comparison[]>('comparison'),
    onlineAnalysis: () => request<OnlineAnalysis>('online-analysis'),
    exportSession: () => request('session/export'),
  };
}
