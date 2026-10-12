import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Profile } from "../types";
import { Cover } from "./Cover";
import { Icon } from "./Icon";

export const Spinner = () => <div className="spinner" role="status" aria-label="Loading" />;

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><h2>{title}</h2>{children && <p>{children}</p>}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="empty error" role="alert">
      <h2>That didn't load</h2>
      <p>{(error as Error)?.message ?? "Something went wrong."}</p>
      {retry && <button className="btn" onClick={retry}>Try again</button>}
    </div>
  );
}

export function BackButton() {
  const nav = useNavigate();
  return <button className="icon-btn back" aria-label="Go back" onClick={() => (history.state?.idx > 0 ? nav(-1) : nav("/"))}><Icon name="back" /></button>;
}

export function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head"><h2>{title}</h2>{action}</div>
      {children}
    </section>
  );
}

/** A row of covers you scroll sideways. */
export function Shelf({ children }: { children: ReactNode }) {
  return <div className="shelf">{children}</div>;
}

export function ShelfItem({ to, onClick, art, title, sub, round }: { to?: string; onClick?: () => void; art: string | null | undefined; title: string; sub?: string; round?: boolean }) {
  const body = (
    <>
      <Cover src={art} round={round} />
      <span className="shelf-title">{title}</span>
      {sub && <span className="shelf-sub">{sub}</span>}
    </>
  );
  return to ? <Link className="shelf-item" to={to}>{body}</Link> : <button className="shelf-item" onClick={onClick}>{body}</button>;
}

/** A list row for an artist, album or playlist (tracks use TrackRow). */
export function LinkRow({ to, art, title, sub, round, icon }: { to: string; art: ReactNode; title: string; sub?: string; round?: boolean; icon?: ReactNode }) {
  return (
    <Link className="row link-row" to={to}>
      <div className={`row-art ${round ? "round" : ""}`}>{art}</div>
      <div className="row-text"><div className="row-title">{title}</div>{sub && <div className="row-sub">{sub}</div>}</div>
      {icon}
    </Link>
  );
}

const AVATARS: Record<string, string> = { blue: "#3A33F5", violet: "#8A3FD1", teal: "#0E8F86", amber: "#C77A0A", rose: "#D13F73", slate: "#4A4D6B" };
export const AVATAR_NAMES = Object.keys(AVATARS);

/** Profiles have no photo: a colour from the palette and the first letter of the name. */
export function Avatar({ profile, size = 40 }: { profile: Pick<Profile, "name" | "avatar">; size?: number }) {
  const known = AVATARS[profile.avatar];
  const fallback = AVATARS[AVATAR_NAMES[(profile.name.charCodeAt(0) || 0) % AVATAR_NAMES.length]]; // a profile with no colour still gets a stable one
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.44, background: known ?? (profile.avatar ? AVATARS.slate : fallback) }} aria-hidden="true">
      {known || !profile.avatar ? profile.name.trim().charAt(0).toUpperCase() : profile.avatar}
    </span>
  );
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<Profile>("/api/me"), staleTime: 60_000 });
}
