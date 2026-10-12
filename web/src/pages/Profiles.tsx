import { useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { resetPlayer } from "../player/engine";
import { AVATAR_NAMES, Avatar, ErrorBox, Spinner } from "../components/Common";
import type { Profile } from "../types";

type Entry = Pick<Profile, "id" | "name" | "avatar">;

export default function Profiles() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const setProfile = useSession((s) => s.setProfile);
  const list = useQuery({ queryKey: ["profiles"], queryFn: () => api.get<Entry[]>("/api/profiles") });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [avatar, setAvatar] = useState(AVATAR_NAMES[0]);

  const choose = (id: number) => {
    resetPlayer();
    qc.clear(); // nothing from the previous profile may linger in the cache
    setProfile(id);
    nav("/", { replace: true });
  };
  const create = useMutation({
    mutationFn: () => api.post<Profile>("/api/profiles", { name: name.trim(), avatar }),
    onSuccess: (p) => choose(p.id),
  });

  if (list.isPending) return <div className="page center"><Spinner /></div>;
  if (list.isError) return <div className="page"><ErrorBox error={list.error} retry={() => list.refetch()} /></div>;
  const none = list.data.length === 0;

  return (
    <div className="page profiles">
      <h1 className="display">{none ? "Welcome to Resonance" : "Who's listening?"}</h1>
      {none && <p className="lede">Make a profile to keep your likes, playlists and listening history. There's no password.</p>}
      <div className="profile-grid">
        {list.data.map((p) => (
          <button key={p.id} className="profile-btn" onClick={() => choose(p.id)}><Avatar profile={p} size={88} /><span>{p.name}</span></button>
        ))}
        {!none && !adding && <button className="profile-btn add" onClick={() => setAdding(true)}><span className="avatar add-avatar" style={{ width: 88, height: 88 }}>+</span><span>Add profile</span></button>}
      </div>
      {(adding || none) && (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
          <label className="field"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} autoFocus autoComplete="off" required /></label>
          <fieldset className="swatches"><legend>Colour</legend>
            {AVATAR_NAMES.map((a) => (
              <label key={a} className={avatar === a ? "on" : ""}>
                <input type="radio" name="avatar" value={a} checked={avatar === a} onChange={() => setAvatar(a)} />
                <Avatar profile={{ name: name || "?", avatar: a }} size={44} /><span className="sr">{a}</span>
              </label>
            ))}
          </fieldset>
          {create.isError && <p className="form-error" role="alert">{(create.error as Error).message}</p>}
          <div className="row-actions">
            <button className="btn primary" disabled={!name.trim() || create.isPending}>Create profile</button>
            {!none && <button type="button" className="btn" onClick={() => setAdding(false)}>Cancel</button>}
          </div>
        </form>
      )}
    </div>
  );
}
