import { create } from "zustand";

export interface Toast { id: number; text: string; kind: "info" | "error" }
let n = 0;

export const useToasts = create<{ list: Toast[] }>(() => ({ list: [] }));

export function toast(text: string, kind: Toast["kind"] = "info") {
  const id = ++n;
  useToasts.setState((s) => ({ list: [...s.list.slice(-2), { id, text, kind }] }));
  setTimeout(() => useToasts.setState((s) => ({ list: s.list.filter((t) => t.id !== id) })), kind === "error" ? 6000 : 3000);
}
