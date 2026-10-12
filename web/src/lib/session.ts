import { create } from "zustand";

const KEY = "resonance.profile";
const read = () => { try { return Number(localStorage.getItem(KEY)) || null; } catch { return null; } };

/** Which profile this device is using. There is no login (D44): picking a profile is the whole sign-in. */
export const useSession = create<{ profileId: number | null; setProfile: (id: number | null) => void }>((set) => ({
  profileId: read(),
  setProfile: (id) => {
    try { id ? localStorage.setItem(KEY, String(id)) : localStorage.removeItem(KEY); } catch { /* private mode */ }
    set({ profileId: id });
  },
}));
export const getProfileId = () => useSession.getState().profileId;
