import { getProfileId, useSession } from "./session";

// The API is on its own subdomain. Unset (dev proxy, or both on one host) means "same origin as this page".
export const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** The profile goes in the query string, not a header: <audio>, WebSockets and keepalive fetches can't all set headers (D10),
 *  and a GET with no custom headers is a "simple" request, so the browser skips the CORS preflight round trip. */
export function url(path: string): string {
  const pid = getProfileId();
  return BASE + path + (pid ? (path.includes("?") ? "&" : "?") + "profile=" + pid : "");
}
export const streamUrl = (videoId: string) => url(`/api/stream/${videoId}`);
export const downloadUrl = (videoId: string) => url(`/api/stream/${videoId}?download=1`);
export const wsUrl = (device: string, name: string) =>
  `${(BASE || location.origin).replace(/^http/, "ws")}/ws?profile=${getProfileId()}&device=${encodeURIComponent(device)}&name=${encodeURIComponent(name)}`;
/** Art and other server-relative paths become absolute; remote thumbnails pass through. */
export const asset = (p: string | null | undefined) => (p ? (p.startsWith("/") ? BASE + p : p) : null);

function detailOf(body: unknown): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e?.msg ?? "").filter(Boolean).join(", ") || "Invalid request";
  return "Something went wrong";
}

type Opts = { body?: unknown; form?: FormData; signal?: AbortSignal; keepalive?: boolean };

async function call<T>(method: string, path: string, o: Opts = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url(path), {
      method, signal: o.signal, keepalive: o.keepalive, credentials: "omit",
      headers: o.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: o.form ?? (o.body !== undefined ? JSON.stringify(o.body) : undefined),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  if (res.status === 401 && getProfileId()) useSession.getState().setProfile(null); // the profile was deleted: back to the picker
  if (!res.ok) throw new ApiError(res.status, detailOf(await res.json().catch(() => null)));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => call<T>("GET", path, { signal }),
  post: <T = void>(path: string, body?: unknown, o?: Opts) => call<T>("POST", path, { body, ...o }),
  put: <T = void>(path: string, body?: unknown) => call<T>("PUT", path, { body }),
  patch: <T = void>(path: string, body?: unknown) => call<T>("PATCH", path, { body }),
  del: <T = void>(path: string, body?: unknown) => call<T>("DELETE", path, { body }),
  upload: <T = void>(path: string, form: FormData, method = "POST") => call<T>(method, path, { form }),
};
