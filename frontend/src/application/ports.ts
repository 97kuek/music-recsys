import type { CatalogPage, Comparison, EventInput, Phase, Playback, Profile, Recommendation, RecommendCommand, Session, StartCommand, Status } from '../domain/contracts';
import type { OnlineAnalysis } from '../domain/onlineAnalysis';

export interface StudyGateway {
  status(): Promise<Status>;
  state(): Promise<Session>;
  catalog(query?: string, offset?: number): Promise<CatalogPage>;
  start(command: StartCommand): Promise<Session>;
  saveProfile(profile: Profile): Promise<unknown>;
  recommend(command: RecommendCommand): Promise<Recommendation>;
  event(event: EventInput & { event_id: string }, keepalive?: boolean): Promise<unknown>;
  progress(phase: Phase, kind?: string, keepalive?: boolean): Promise<unknown>;
  next(period: number): Promise<Session>;
  finish(): Promise<unknown>;
  logout(): Promise<unknown>;
  remove(): Promise<unknown>;
  playback(item: string, request?: string): Promise<Playback>;
  mediaUrl(path: string): string;
  comparison(): Promise<Comparison[]>;
  onlineAnalysis(): Promise<OnlineAnalysis>;
  exportSession(): Promise<unknown>;
}

export interface ProfileStorage {
  forgetLegacyProfile(catalogKey: string): void;
}
