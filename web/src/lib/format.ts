export function duration(ms: number | null | undefined): string {
  if (!ms || ms < 0) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function bytes(n: number): string {
  if (n < 1e9) return `${Math.round(n / 1e6)} MB`;
  return `${(n / 1e9).toFixed(n < 1e10 ? 1 : 0)} GB`;
}

export function greeting(d = new Date()): string {
  const h = d.getHours();
  return h < 5 ? "Still up?" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export const names = (a: { name: string }[]) => a.map((x) => x.name).join(", ");
