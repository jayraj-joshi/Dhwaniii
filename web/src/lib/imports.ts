import { create } from "zustand";

/** Progress of playlist imports, pushed over the WebSocket (`import_progress`). */
export const useImports = create<{ progress: Record<number, { done: number; total: number }> }>(() => ({ progress: {} }));
