// A device is a browser install: a random id kept in localStorage (D21) and a name the user can change.
const ID = "resonance.device";
const NAME = "resonance.device-name";
const get = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const set = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

function uuid() {
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID(); // only exists on secure origins
  return Array.from({ length: 4 }, () => Math.random().toString(36).slice(2, 10)).join("-");
}

export const deviceId = (() => { let id = get(ID); if (!id) { id = uuid(); set(ID, id); } return id; })();

function guess() {
  const ua = navigator.userAgent;
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${browser} on ${os}`;
}
export const deviceName = () => get(NAME) || guess();
export const setDeviceName = (n: string) => set(NAME, n.trim().slice(0, 60));
