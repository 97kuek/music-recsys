import type { ProfileStorage } from '../application/ports';

export const profileStorage: ProfileStorage = {
  forgetLegacyProfile(catalogKey) {
    try { localStorage.removeItem(`music-profile-v1:${catalogKey}`); } catch { /* Storage can be disabled. */ }
  },
};
