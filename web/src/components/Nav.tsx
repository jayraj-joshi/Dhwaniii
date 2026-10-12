import { NavLink } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { PlaylistSummary } from "../types";
import { PlaylistCover } from "./Cover";
import { Avatar, useMe } from "./Common";
import { Icon, type IconName } from "./Icon";

const TABS: { to: string; label: string; icon: IconName }[] = [
  { to: "/", label: "Home", icon: "home" },
  { to: "/search", label: "Search", icon: "search" },
  { to: "/library", label: "Your library", icon: "library" },
];

export function TabBar() {
  return (
    <nav className="tabbar" aria-label="Main">
      {TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.to === "/"} className="tab"><Icon name={t.icon} size={26} /><span>{t.label.replace("Your l", "L")}</span></NavLink>
      ))}
    </nav>
  );
}

export function Sidebar() {
  const me = useMe();
  const lists = useQuery({ queryKey: ["playlists"], queryFn: () => api.get<PlaylistSummary[]>("/api/playlists") });
  return (
    <aside className="sidebar">
      <NavLink to="/" className="brand"><img src="/icon.svg" alt="" width="28" height="28" />Resonance</NavLink>
      <nav aria-label="Main">
        {TABS.map((t) => <NavLink key={t.to} to={t.to} end={t.to === "/"} className="side-link"><Icon name={t.icon} />{t.label}</NavLink>)}
        <NavLink to="/liked" className="side-link"><Icon name="heart" />Liked Songs</NavLink>
      </nav>
      <div className="side-lists" aria-label="Playlists">
        {lists.data?.map((p) => (
          <NavLink key={p.id} to={`/playlist/${p.id}`} className="side-list"><PlaylistCover cover={p.cover_url} collage={p.collage} className="mini-cover" /><span>{p.name}</span></NavLink>
        ))}
      </div>
      <NavLink to="/settings" className="side-link side-me">{me.data ? <Avatar profile={me.data} size={28} /> : <Icon name="settings" />}{me.data?.name ?? "Settings"}</NavLink>
    </aside>
  );
}
