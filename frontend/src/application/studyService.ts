import type { ProfileStorage, StudyGateway } from './ports';
import { createEventOutbox } from './eventOutbox.ts';
import type { Profile, RecommendCommand } from '../domain/contracts';

// Framework-free actions preserve ordering across persistence, navigation and inference.
export function createStudyService(gateway: StudyGateway, newId: () => string, storage: ProfileStorage) {
  const outbox = createEventOutbox((event, keepalive) => gateway.event(event, keepalive), newId);
  return {
    ...gateway,
    forgetLegacyProfile: (catalogKey: string) => storage.forgetLegacyProfile(catalogKey),
    emit: (event: Parameters<typeof outbox.emit>[0]) => outbox.emit({ event_version: 2, ...event }),
    flushEvents: outbox.flush,
    async start(command: Parameters<StudyGateway['start']>[0]) {
      const session = await gateway.start(command);
      outbox.clear();
      return session;
    },
    async logout() {
      await outbox.flush();
      const response = await gateway.logout();
      outbox.clear();
      return response;
    },
    async remove() {
      const response = await gateway.remove();
      outbox.clear();
      return response;
    },
    async beginListening(profile: Profile, command: RecommendCommand, beforeRequest?: () => void) {
      await gateway.saveProfile(profile);
      await gateway.progress('listen');
      beforeRequest?.();
      return gateway.recommend(command);
    },
  };
}
export type StudyService = ReturnType<typeof createStudyService>;
