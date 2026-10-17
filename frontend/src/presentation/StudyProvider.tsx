import { createContext, useContext, type ReactNode } from 'react';
import type { StudyService } from '../application/studyService';

const Context = createContext<StudyService | null>(null);
export function StudyProvider({ service, children }: { service: StudyService; children: ReactNode }) {
  return <Context.Provider value={service}>{children}</Context.Provider>;
}
export function useStudy(): StudyService {
  const service = useContext(Context);
  if (!service) throw new Error('StudyProvider is required');
  return service;
}
