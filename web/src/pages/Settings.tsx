import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BASE, api } from "../lib/api";
import { deviceName, setDeviceName } from "../lib/device";
import { bytes } from "../lib/format";
import { isIos, isStandalone, useInstall } from "../lib/install";
import { useSession } from "../lib/session";
import { applyTheme } from "../lib/theme";
import { toast } from "../lib/toast";
import { resetPlayer, usePlayer } from "../player/engine";
import { AVATAR_NAMES, Avatar, ErrorBox, Spinner, useMe } from "../components/Common";
import type { AutoplayMethod, Prefs, SettingsData } from "../types";

const THEMES = [["system", "System"], ["light", "Light"], ["dark", "Dark"]] as const;
const AUTOPLAY_METHODS: Record<AutoplayMethod, [string, string]> = {
  gemini: ["Gemini", "Google's Gemini picks similar songs from what it knows. Fast."],
  tavily: ["Web search", "Searches the web for songs people recommend, then an AI model reads the results."],
};

export default function Settings() {
  const me = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const setProfile = useSession((s) => s.setProfile);
  const install = useInstall((s) => s.event);
  const devices = usePlayer((s) => s.devices);
  const storage = useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsData>("/api/settings") });
  const [name, setName] = useState<string | null>(null);
  const [device, setDevice] = useState(deviceName());

  if (me.isPending) return <div className="page"><Spinner /></div>;
  if (me.isError) return <div className="page"><ErrorBox error={me.error} retry={() => me.refetch()} /></div>;
  const m = me.data;
  const prefs: Prefs = m.prefs ?? {};

  const save = async (body: { name?: string; avatar?: string; prefs?: Prefs }) => {
    try { await api.patch("/api/me", body); await qc.invalidateQueries({ queryKey: ["me"] }); return true; }
    catch (e) { toast((e as Error).message, "error"); return false; }
  };
  const setPref = (p: Prefs) => { void save({ prefs: p }); };
  const used = storage.data ? storage.data.storage_used_bytes / storage.data.storage_limit_bytes : 0;
  const methods = storage.data?.autoplay_methods ?? [];
  const method = prefs.autoplay_method && methods.includes(prefs.autoplay_method) ? prefs.autoplay_method : methods[0]; // as the server picks it

  return (
    <div className="page settings">
      <h1 className="display">Settings</h1>

      <section className="group" aria-labelledby="s-profile">
        <h2 id="s-profile">Profile</h2>
        <div className="profile-line"><Avatar profile={m} size={56} />
          <form className="inline-form" onSubmit={async (e) => { e.preventDefault(); if (name?.trim() && await save({ name: name.trim() })) { setName(null); toast("Name saved"); } }}>
            <input value={name ?? m.name} onChange={(e) => setName(e.target.value)} maxLength={40} aria-label="Profile name" />
            {name !== null && name.trim() !== m.name && <button className="btn primary" disabled={!name.trim()}>Save</button>}
          </form>
        </div>
        <fieldset className="swatches"><legend>Colour</legend>
          {AVATAR_NAMES.map((a) => (
            <label key={a} className={m.avatar === a ? "on" : ""}>
              <input type="radio" name="avatar" checked={m.avatar === a} onChange={() => void save({ avatar: a })} />
              <Avatar profile={{ name: m.name, avatar: a }} size={40} /><span className="sr">{a}</span>
            </label>
          ))}
        </fieldset>
        <button className="btn" onClick={() => { resetPlayer(); qc.clear(); setProfile(null); nav("/profiles"); }}>Switch profile</button>
      </section>

      <section className="group" aria-labelledby="s-look">
        <h2 id="s-look">Appearance</h2>
        <div className="segmented" role="radiogroup" aria-label="Theme">
          {THEMES.map(([id, label]) => (
            <label key={id} className={(prefs.theme ?? "system") === id ? "on" : ""}>
              <input type="radio" name="theme" checked={(prefs.theme ?? "system") === id} onChange={() => { applyTheme(id); setPref({ theme: id }); }} />{label}
            </label>
          ))}
        </div>
      </section>

      <section className="group" aria-labelledby="s-play">
        <h2 id="s-play">Playback</h2>
        <label className="switch-row">
          <span><span className="row-title">Autoplay</span><span className="row-sub">When a list ends, keep going with songs like the last one.</span></span>
          <input type="checkbox" role="switch" checked={prefs.autoplay !== false} onChange={(e) => { usePlayer.setState({ autoplay: e.target.checked }); setPref({ autoplay: e.target.checked }); }} />
        </label>
        {prefs.autoplay !== false && methods.length > 1 && method && (
          <>
            <div className="segmented" role="radiogroup" aria-label="How autoplay finds songs">
              {methods.map((id) => (
                <label key={id} className={method === id ? "on" : ""}>
                  <input type="radio" name="autoplay-method" checked={method === id} onChange={() => setPref({ autoplay_method: id })} />{AUTOPLAY_METHODS[id][0]}
                </label>
              ))}
            </div>
            <p className="muted">{AUTOPLAY_METHODS[method][1]}</p>
          </>
        )}
      </section>

      <section className="group" aria-labelledby="s-device">
        <h2 id="s-device">This device</h2>
        <label className="field"><span>Device name</span>
          <input value={device} onChange={(e) => setDevice(e.target.value)} onBlur={() => { setDeviceName(device); toast("Device name saved. It updates the next time the app starts."); }} maxLength={60} />
        </label>
        {devices.length > 1 && <p className="muted">{`Also signed in on: ${devices.filter((d) => d.name !== device).map((d) => d.name).join(", ")}`}</p>}
        {!isStandalone() && (install
          ? <button className="btn primary" onClick={() => void install.prompt()}>Install Resonance</button>
          : isIos() && <p className="muted">To install: tap the Share button in Safari, then Add to Home Screen.</p>)}
      </section>

      <section className="group" aria-labelledby="s-store">
        <h2 id="s-store">Storage</h2>
        {storage.data ? (
          <>
            <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(used * 100)} aria-label="Storage used"><i style={{ width: `${Math.min(100, used * 100)}%` }} /></div>
            <p>{`${bytes(storage.data.storage_used_bytes)} of ${bytes(storage.data.storage_limit_bytes)} used by ${storage.data.file_count} downloaded songs`}</p>
            <p className="muted">{`Songs are saved as ${storage.data.codec}. When the limit is reached, the songs you haven't played for longest are removed, except liked ones.`}</p>
          </>
        ) : storage.isError ? <ErrorBox error={storage.error} retry={() => storage.refetch()} /> : <Spinner />}
      </section>

      <section className="group" aria-labelledby="s-lib">
        <h2 id="s-lib">Library</h2>
        <Link className="btn" to="/import">Import a playlist</Link>
      </section>

      <p className="muted fine">{`Connected to ${BASE || location.origin}`}</p>
    </div>
  );
}
