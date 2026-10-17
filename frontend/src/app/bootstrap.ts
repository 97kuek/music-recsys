import { profileStorage } from '../infrastructure/profileStorage';
import { createStudyService } from '../application/studyService';
import { createStudyGateway } from '../infrastructure/studyGateway';
import { request, apiUrl } from '../infrastructure/httpTransport';

export const studyService = createStudyService(createStudyGateway(
  import.meta.env.VITE_PUBLIC_DEMO === '1'
    ? async <T>(path: string, method = 'GET', body?: unknown) => {
      const { browserDemo } = await import('../infrastructure/browserDemo');
      return await browserDemo(path, method, body) as T;
    } : request,
  apiUrl,
), () => crypto.randomUUID(), profileStorage);
