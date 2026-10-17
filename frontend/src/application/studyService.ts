import type { ProfileStorage, StudyGateway } from './ports';
import type { EventInput, Profile, RecommendCommand } from '../domain/contracts';

// Framework-free actions preserve ordering across persistence, navigation and inference.
export function createStudyService(gateway: StudyGateway, newId: () => string, storage: ProfileStorage) {
  return {
    ...gateway,
    forgetLegacyProfile: (catalogKey: string) => storage.forgetLegacyProfile(catalogKey),
    emit: (event: EventInput) => gateway.event({ ...event, event_id: newId() }),
    async beginListening(profile: Profile, command: RecommendCommand) {
      await gateway.saveProfile(profile);
      await gateway.progress('listen');
      return gateway.recommend(command);
    },
  };
}
export type StudyService = ReturnType<typeof createStudyService>;
