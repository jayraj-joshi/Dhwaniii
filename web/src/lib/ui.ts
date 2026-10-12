import { create } from "zustand";
import type { PlayItem } from "../player/engine";

export interface MenuTarget {
  item: PlayItem;
  /** Set when the row is inside one of the user's playlists, which adds remove and reorder. */
  playlist?: { id: number; itemId: number; firstItemId: number; index: number; count: number };
}

export const useUi = create<{ menu: MenuTarget | null; picker: PlayItem | null; queue: boolean; full: boolean }>(() => ({
  menu: null, picker: null, queue: false, full: false,
}));

export const openMenu = (t: MenuTarget) => useUi.setState({ menu: t });
export const closeMenu = () => useUi.setState({ menu: null });
export const openPicker = (item: PlayItem) => useUi.setState({ menu: null, picker: item });
export const closePicker = () => useUi.setState({ picker: null });
export const setQueueOpen = (queue: boolean) => useUi.setState({ queue });
export const setFull = (full: boolean) => useUi.setState({ full });
