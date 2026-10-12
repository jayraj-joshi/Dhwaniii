import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { useSession } from "./lib/session";
import { applyTheme } from "./lib/theme";
import { connect, disconnect } from "./lib/ws";
import { cycleRepeat, onServerMessage, seek, skipNext, skipPrev, startEngine, togglePlay, toggleShuffle, usePlayer, getPositionMs, setVolume } from "./player/engine";
import { toggleLike, useTracks } from "./lib/tracks";
import { setQueueOpen, useUi } from "./lib/ui";
import { DesktopBar, FullPlayer, MiniPlayer } from "./components/Player";
import { Sidebar, TabBar } from "./components/Nav";
import { Sheets } from "./components/Sheets";
import { Toasts } from "./components/Toasts";
import { useMe } from "./components/Common";
import Album from "./pages/Album";
import Artist from "./pages/Artist";
import Home from "./pages/Home";
import Import from "./pages/Import";
import Liked from "./pages/Liked";
import Library from "./pages/Library";
import Playlist from "./pages/Playlist";
import Profiles from "./pages/Profiles";
import Search from "./pages/Search";
import Settings from "./pages/Settings";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, networkMode: "always", // offline should fail fast and show the error, not hang on a spinner
      retry: (n, e) => { const status = (e as { status?: number }).status ?? 500; return n < 1 && (status === 0 || status >= 500); },
    },
    mutations: { networkMode: "always" },
  },
});

/** Keyboard shortcuts (§10.4). Ignored while typing in a field. */
function useShortcuts() {
  const nav = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); nav("/search"); setTimeout(() => document.getElementById("search-input")?.focus(), 50); return; }
      if (e.metaKey || e.ctrlKey || e.altKey || el.closest("dialog, [role=slider], button, a")) return;
      const p = usePlayer.getState();
      switch (e.key) {
        case " ": e.preventDefault(); togglePlay(); break;
        case "ArrowRight": e.preventDefault(); e.shiftKey ? skipNext() : seek(getPositionMs() + 5000); break;
        case "ArrowLeft": e.preventDefault(); e.shiftKey ? skipPrev() : seek(getPositionMs() - 5000); break;
        case "ArrowUp": e.preventDefault(); setVolume(Math.min(1, p.volume + 0.05)); break;
        case "ArrowDown": e.preventDefault(); setVolume(Math.max(0, p.volume - 0.05)); break;
        case "l": case "L": { const t = useTracks.getState().byId[p.s.current?.track_id ?? -1]; if (t) toggleLike({ ...t, liked: useTracks.getState().liked[t.id] ?? t.liked }); break; }
        case "s": case "S": toggleShuffle(); break;
        case "r": case "R": cycleRepeat(); break;
        case "q": case "Q": setQueueOpen(!useUi.getState().queue); break;
      }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [nav]);
}

/** Everything that exists only while a profile is chosen: the player, the socket and the profile's saved preferences. */
function Session() {
  const qc = useQueryClient();
  const me = useMe();
  const { pathname } = useLocation();
  useShortcuts();

  useEffect(() => {
    startEngine();
    connect(onServerMessage, (reconnected) => { if (reconnected) void qc.invalidateQueries(); }); // events may have been missed while offline
    return () => disconnect();
  }, [qc]);

  useEffect(() => { if (me.data) { applyTheme(me.data.prefs.theme ?? "system"); usePlayer.setState({ autoplay: me.data.prefs.autoplay !== false }); } }, [me.data]);
  useEffect(() => { document.getElementById("main")?.scrollTo(0, 0); }, [pathname]);

  return (
    <div className="app">
      <Sidebar />
      <main id="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/search" element={<Search />} />
          <Route path="/library" element={<Library />} />
          <Route path="/liked" element={<Liked />} />
          <Route path="/playlist/:id" element={<Playlist />} />
          <Route path="/album/:id" element={<Album />} />
          <Route path="/artist/:id" element={<Artist />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/import" element={<Import />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <div className="dock"><MiniPlayer /><TabBar /></div>
      <DesktopBar />
      <FullPlayer />
      <Sheets />
    </div>
  );
}

export default function App() {
  const profileId = useSession((s) => s.profileId);
  return (
    <QueryClientProvider client={queryClient}>
      {profileId ? (
        <Routes>
          <Route path="/profiles" element={<Profiles />} />
          <Route path="*" element={<Session />} />
        </Routes>
      ) : (
        <Routes><Route path="*" element={<Profiles />} /></Routes>
      )}
      <Toasts />
    </QueryClientProvider>
  );
}
