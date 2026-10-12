import { create } from "zustand";
import { wsUrl } from "./api";
import { deviceId, deviceName } from "./device";

// One socket per tab (§8). It reconnects with backoff, and again at once when the phone wakes up or the network returns.
export const useConn = create<{ online: boolean }>(() => ({ online: false }));

let sock: WebSocket | null = null;
let onMessage: (m: any) => void = () => {};
let onOpen: (reconnected: boolean) => void = () => {};
let retry: ReturnType<typeof setTimeout> | undefined;
let ping: ReturnType<typeof setInterval> | undefined;
let tries = 0;
let wanted = false;
let opened = false;

function open() {
  if (!wanted || (sock && sock.readyState <= WebSocket.OPEN)) return;
  const ws = (sock = new WebSocket(wsUrl(deviceId, deviceName())));
  ws.onopen = () => {
    tries = 0;
    useConn.setState({ online: true });
    clearInterval(ping);
    ping = setInterval(() => send({ type: "ping" }), 25_000);
    onOpen(opened);
    opened = true;
  };
  ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch { /* ignore a malformed frame */ } };
  ws.onclose = () => {
    if (sock === ws) sock = null;
    clearInterval(ping);
    useConn.setState({ online: false });
    if (wanted) retry = setTimeout(open, Math.min(30_000, 1000 * 2 ** tries++));
  };
  ws.onerror = () => ws.close();
}

const wake = () => { if (wanted && !sock && document.visibilityState !== "hidden") { clearTimeout(retry); open(); } };

export function connect(handler: (m: any) => void, opened: (reconnected: boolean) => void) {
  onMessage = handler;
  onOpen = opened;
  wanted = true;
  addEventListener("online", wake);
  document.addEventListener("visibilitychange", wake);
  open();
}

export function disconnect() {
  wanted = false;
  opened = false;
  clearTimeout(retry);
  removeEventListener("online", wake);
  document.removeEventListener("visibilitychange", wake);
  sock?.close();
  sock = null;
  useConn.setState({ online: false });
}

export function send(msg: object) {
  if (sock?.readyState === WebSocket.OPEN) sock.send(JSON.stringify(msg));
}
