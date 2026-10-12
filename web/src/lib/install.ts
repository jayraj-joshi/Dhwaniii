import { create } from "zustand";

interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

/** Chrome and Android offer a real install prompt; iOS has none, so Settings explains Share → Add to Home Screen instead. */
export const useInstall = create<{ event: InstallEvent | null }>(() => ({ event: null }));

addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); useInstall.setState({ event: e as InstallEvent }); });
addEventListener("appinstalled", () => useInstall.setState({ event: null }));

export const isStandalone = () => matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
export const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
