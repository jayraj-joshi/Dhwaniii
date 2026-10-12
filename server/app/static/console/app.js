// Resonance test console: plain JavaScript, no build. Every element is made with createElement and text nodes, never from an HTML string,
// because track titles come from YouTube and must not be able to inject markup.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const state = {
  pid: Number(localStorage.getItem("pid")) || null,
  device: localStorage.getItem("device") || (localStorage.setItem("device", crypto.randomUUID()), localStorage.getItem("device")),
  live: {},            // video_id -> {state, percent}   (from the WebSocket)
  playlists: [],       // the profile's playlists, for the "add to playlist" menus
  ensured: {},         // video_id -> Track, so a YouTube result is saved once
  ws: null, wsTimer: null, wsDelay: 1000, serverState: null, current: null,
};
const audio = $("#player");

// ---------------------------------------------------------------- small helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const clear = (el) => { el.replaceChildren(); return el; };
// like h()'s children: flattens arrays and skips null/false. Native append() would print an array as "[object HTMLElement]" and null as "null".
const add = (el, ...kids) => { for (const k of kids.flat(Infinity)) if (k != null && k !== false) el.append(k.nodeType ? k : String(k)); return el; };
const names = (arr) => (arr || []).map((a) => a.name).join(", ");
const mmss = (ms) => (ms == null ? "" : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`);
const ago = (iso) => (iso ? new Date(iso).toLocaleString() : "");
const json = (v) => JSON.stringify(v, null, 2);

function log(line) {
  const el = $("#log");
  el.textContent = `${new Date().toLocaleTimeString()}  ${line}\n` + el.textContent.split("\n").slice(0, 60).join("\n");
}

async function api(path, { method = "GET", body, form, quiet } = {}) {
  const headers = {};
  if (state.pid) headers["X-Profile-Id"] = state.pid;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const t0 = performance.now();
  const r = await fetch(path, { method, headers, body: payload });
  log(`${method} ${path} → ${r.status} (${Math.round(performance.now() - t0)} ms)`);
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) {
    const msg = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail ?? r.status);
    if (!quiet) toast(`${method} ${path}: ${msg}`, true);
    throw new Error(msg);
  }
  return data;
}

function toast(text, bad) {
  const t = h("div", { class: bad ? "err" : "ok" }, text);
  $("#search-stats").after(t);
  setTimeout(() => t.remove(), 6000);
}

// ---------------------------------------------------------------- download badges (updated live from the socket)
const BADGE = { cached: "● cached", downloading: "◌ downloading", failed: "⚠ failed", remote: "☁ not downloaded" };
function paint(el, v) {
  el.className = `badge ${v.state}`;
  el.textContent = v.state === "downloading" && v.percent != null ? `◌ ${v.percent}%` : BADGE[v.state];
}
function badge(vid, fallback) {
  const el = h("span", { "data-vid": vid });
  paint(el, state.live[vid] || { state: fallback || "remote" });
  return el;
}
function setLive(vid, v) {
  state.live[vid] = v;
  document.querySelectorAll(`[data-vid="${CSS.escape(vid)}"]`).forEach((el) => paint(el, v));
}

// ---------------------------------------------------------------- tracks: ensure, play, like, add to playlist
const HINT = ["video_id", "title", "artists", "album", "duration_ms", "thumb_url", "channel", "quality_score"];
async function asTrack(item) {
  if (item.cache_state) return item;                                   // already a library Track
  if (state.ensured[item.video_id]) return state.ensured[item.video_id];
  const hint = Object.fromEntries(HINT.filter((k) => item[k] !== undefined).map((k) => [k, item[k]]));
  const [t] = await api("/api/tracks/ensure", { method: "POST", body: [hint] });
  return (state.ensured[t.video_id] = t);
}

async function reportPlay(skipped) {
  const c = state.current;
  if (!c) return;
  state.current = null;
  const ms = Math.round((audio.currentTime || 0) * 1000);
  if (ms < 1000) return;
  api("/api/me/history", { method: "POST", quiet: true, body: [{ track_id: c.track.id, played_at: new Date().toISOString(), ms_played: ms, skipped, context: c.context, device_id: state.device }] }).catch(() => {});
}

async function play(item, context = "search") {
  const t = await asTrack(item);
  await reportPlay(true);
  state.current = { track: t, context };
  const t0 = performance.now();
  audio.onplaying = () => { audio.onplaying = null; log(`first sound after ${Math.round(performance.now() - t0)} ms`); };
  audio.src = `/api/stream/${t.video_id}?profile=${state.pid}`;
  $("#now").textContent = `${t.title} — ${names(t.artists)}`;
  try { await audio.play(); } catch (e) { toast(`Playback failed: ${e.message}`, true); }
  if (!state.live[t.video_id] && t.cache_state !== "cached") setLive(t.video_id, { state: "downloading" });
  send({ type: "queue_update", state: { context: { type: context.split(":")[0], id: context.split(":")[1] || "", name: context }, context_tracks: [t.id], order: [0], context_pos: 0,
                                         user_queue: [], current: { track_id: t.id, from: "context" }, shuffle: false, repeat: "off", position_ms: 0, playing: true } });
  sendPlayerState();
}

function heart(item) {
  const b = h("button", { title: "Like" }, item.liked ? "♥" : "♡");
  b.addEventListener("click", async () => {
    const t = await asTrack(item);
    const liked = b.textContent === "♡";
    await api(`/api/me/likes/${t.id}`, { method: liked ? "PUT" : "DELETE" });
    b.textContent = liked ? "♥" : "♡";
    item.liked = liked;
  });
  return b;
}

function addMenu(item) {
  const sel = h("select", { title: "Add to playlist" });
  const fill = () => {  // rebuilt whenever it is opened, so playlists made after this row was drawn still show up
    const keep = sel.value;
    add(clear(sel), h("option", { value: "" }, "+ playlist"), state.playlists.map((p) => h("option", { value: p.id }, p.name)));
    sel.value = keep;
  };
  fill();
  sel.addEventListener("focus", fill);
  sel.addEventListener("mousedown", fill);
  sel.addEventListener("change", async () => {
    if (!sel.value) return;
    const t = await asTrack(item);
    await api(`/api/playlists/${sel.value}/tracks`, { method: "POST", body: { track_ids: [t.id] } });
    toast(`Added “${t.title}”`);
    sel.value = "";
  });
  return sel;
}

// a row for either a library Track or a YouTube/remote result
function trackRow(item, context, extra) {
  const isTrack = !!item.cache_state;
  return h("li", {},
    h("button", { class: "primary", title: "Play", onclick: () => play(item, context) }, "▶"),
    h("div", { class: "grow" }, h("b", {}, item.title), h("span", { class: "muted" }, [names(item.artists), item.album && ` · ${item.album.title}`, item.duration_ms && ` · ${mmss(item.duration_ms)}`])),
    extra,
    badge(item.video_id, isTrack ? item.cache_state : "remote"),
    !isTrack && item.in_library ? h("span", { class: "pill" }, "in library") : null,
    heart(item), addMenu(item));
}

// ---------------------------------------------------------------- tabs
const TABS = { search: "Search", library: "Library", home: "Home", stats: "Stats & Wrapped", import: "Import", live: "Devices & live", admin: "Admin", explorer: "API explorer" };
function showTab(name) {
  localStorage.setItem("tab", name);
  for (const k of Object.keys(TABS)) $(`#tab-${k}`).hidden = k !== name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  ({ library: loadLibrary, home: loadHome, admin: loadAdmin }[name] || (() => {}))();
}
const tabs = $("#tabs");
for (const [k, label] of Object.entries(TABS)) tabs.append(h("button", { "data-tab": k, onclick: () => showTab(k) }, label));

// ---------------------------------------------------------------- profiles
async function loadProfiles() {
  const list = await api("/api/profiles", { quiet: true });
  const sel = clear($("#profile"));
  if (!list.length) sel.append(h("option", { value: "" }, "(none yet: create one)"));
  for (const p of list) sel.append(h("option", { value: p.id }, `${p.avatar || ""} ${p.name} (#${p.id})`.trim()));
  if (!list.some((p) => p.id === state.pid)) state.pid = list[0]?.id ?? null;
  sel.value = state.pid ?? "";
  selectProfile(state.pid);
}
async function selectProfile(id) {
  state.pid = id ? Number(id) : null;
  state.live = {}; state.ensured = {};
  localStorage.setItem("pid", state.pid ?? "");
  if (!state.pid) return;
  await refreshPlaylists();
  connect();
  showTab(localStorage.getItem("tab") in TABS ? localStorage.getItem("tab") : "search");
}
let playlistSeq = 0;
async function refreshPlaylists() {  // overlapping refreshes: only the newest answer may win
  const n = ++playlistSeq;
  const list = await api("/api/playlists", { quiet: true }).catch(() => null);
  if (n === playlistSeq && list) state.playlists = list;
}
$("#profile").addEventListener("change", (e) => selectProfile(e.target.value));
$("#new-profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = await api("/api/profiles", { method: "POST", body: { name: new FormData(e.target).get("name") } });
  e.target.reset();
  state.pid = p.id;
  await loadProfiles();
});

// ---------------------------------------------------------------- search
$("#search-form").addEventListener("submit", (e) => { e.preventDefault(); runSearch(new FormData(e.target)); });
async function runSearch(fd, remote) {
  const out = clear($("#search-results"));
  const params = new URLSearchParams({ q: fd.get("q"), type: fd.get("type"), remote: remote || fd.get("remote") });
  const t0 = performance.now();
  let r;
  try { r = await api(`/api/search?${params}`); } catch { return; }
  $("#search-stats").textContent = `${Math.round(performance.now() - t0)} ms · “${r.query_norm}” · ${r.cache_hit ? "library HIT" : "library miss"}`;
  const section = (title, items, render) => items.length && out.append(h("h3", {}, title), h("ul", { class: "list" }, items.map(render)));
  if (r.top) out.append(h("div", { class: "card" }, h("h4", {}, "Top result"), r.top.kind === "artist" ? artistRow(r.top.item) : h("ul", { class: "list" }, trackRow(r.top.item, "search"))));
  section("Songs in your library", r.songs, (t) => trackRow(t, "search"));
  section("Artists", r.artists, artistRow);
  section("Albums", r.albums, albumRow);
  section("Playlists", r.playlists, (p) => h("li", {}, h("div", { class: "grow" }, h("b", {}, p.name), h("span", { class: "muted" }, `${p.track_count} tracks`))));
  section("YouTube artists", r.youtube_artists, (a) => artistRow({ id: a.id, name: a.name, ytm: a.ytm_channel_id }));
  section("YouTube albums", r.youtube_albums, (a) => albumRow({ id: a.id, title: a.title, ytm: a.ytm_browse_id, artist: a.artist && { name: a.artist }, year: a.year }));
  section("From YouTube", r.youtube, (y) => trackRow(y, "search", y.quality_score != null ? h("span", { class: "pill", title: "ranking score" }, y.quality_score.toFixed(1)) : null));
  out.append(h("p", {}, h("button", { onclick: () => runSearch(fd, "always") }, "More from YouTube (remote=always)")));
  if (!out.children.length) out.append(h("p", { class: "muted" }, "Nothing found."));
  api("/api/me/searches", { method: "POST", body: { query: fd.get("q") }, quiet: true }).catch(() => {});
}
const artistRow = (a) => h("li", {}, h("div", { class: "grow" }, h("b", {}, a.name)), h("button", { onclick: () => openArtist(a.id ?? `ytm:${a.ytm}`) }, "Open artist"));
const albumRow = (a) => h("li", {}, h("div", { class: "grow" }, h("b", {}, a.title), h("span", { class: "muted" }, [a.artist?.name, a.year && ` · ${a.year}`])), h("button", { onclick: () => openAlbum(a.id ?? `ytm:${a.ytm}`) }, "Open album"));

// ---------------------------------------------------------------- artist / album detail panel
function showDetail(...nodes) { $("#detail").hidden = false; add(clear($("#detail-body")), nodes); }
$("#detail-close").addEventListener("click", () => { $("#detail").hidden = true; });

async function openArtist(ident) {
  let a;
  try { a = await api(`/api/artists/${ident}`); } catch { return; }
  const follow = h("button", { onclick: async () => { await api(`/api/me/follows/${a.id}`, { method: a.followed ? "DELETE" : "PUT" }); a.followed = !a.followed; follow.textContent = a.followed ? "Following ✓" : "Follow"; } }, a.followed ? "Following ✓" : "Follow");
  showDetail(h("h3", {}, a.name), h("div", { class: "row" }, follow, h("span", { class: "pill" }, `${a.plays_30d} plays in 30 days`)),
    a.bio ? h("p", { class: "muted" }, a.bio.slice(0, 400) + (a.bio.length > 400 ? "…" : "")) : null,
    h("h4", {}, "Popular"), h("ul", { class: "list" }, a.popular.map((t) => trackRow(t, `artist:${a.id}`, t.plays ? h("span", { class: "pill" }, `${t.plays}×`) : null))),
    h("h4", {}, "Discography"), h("ul", { class: "list" }, a.discography.map((d) => h("li", {}, h("div", { class: "grow" }, h("b", {}, d.title), h("span", { class: "muted" }, `${d.type}${d.year ? ` · ${d.year}` : ""}`)), h("button", { onclick: () => openAlbum(d.id ?? `ytm:${d.ytm_browse_id}`) }, "Open")))),
    a.fans_also_like.length ? [h("h4", {}, "Fans also like"), h("ul", { class: "list" }, a.fans_also_like.map((f) => artistRow({ id: f.id, name: f.name, ytm: f.ytm_channel_id })))] : null);
}

async function openAlbum(ident) {
  let a;
  try { a = await api(`/api/albums/${ident}`); } catch { return; }
  const save = h("button", { onclick: async () => { await api(`/api/me/albums/${a.id}`, { method: a.saved ? "DELETE" : "PUT" }); a.saved = !a.saved; save.textContent = a.saved ? "Saved ✓" : "Save album"; } }, a.saved ? "Saved ✓" : "Save album");
  const playAll = h("button", { class: "primary", onclick: async () => { const ts = []; for (const t of a.tracks) ts.push(await asTrack(t)); toast(`Saved ${ts.length} tracks`); play(ts[0], `album:${a.id}`); } }, "Save all & play");
  showDetail(h("h3", {}, a.title), h("p", { class: "muted" }, [a.artist?.name, a.year && ` · ${a.year}`]), h("div", { class: "row" }, playAll, save),
    h("ul", { class: "list" }, a.tracks.map((t) => trackRow(t, `album:${a.id}`, h("span", { class: "pill" }, `#${t.track_number}`)))));
}

// ---------------------------------------------------------------- library
let librarySeq = 0;
async function loadLibrary() {
  const run = ++librarySeq;
  await refreshPlaylists();
  const [lib, likes, recent, searches] = await Promise.all([api("/api/me/library"), api("/api/me/likes?limit=50"), api("/api/me/recent"), api("/api/me/searches")]).catch(() => []);
  if (!lib || run !== librarySeq) return;  // a newer refresh is already drawing
  const out = clear($("#library"));
  out.append(h("p", {}, `${lib.liked_count} liked · ${lib.playlists.length} playlists · ${lib.artists.length} followed artists · ${lib.albums.length} saved albums`));
  out.append(h("h3", {}, "Playlists"), h("ul", { class: "list" }, lib.playlists.map((p) => h("li", {},
    h("div", { class: "grow" }, h("b", {}, `${p.pinned_to_sidebar ? "📌 " : ""}${p.name}`), h("span", { class: "muted" }, `${p.track_count} tracks`)),
    h("button", { onclick: () => openPlaylist(p.id) }, "Open"), h("button", { onclick: async () => { await api(`/api/playlists/${p.id}`, { method: "DELETE" }); loadLibrary(); } }, "Delete")))));
  out.append(h("h3", {}, "Liked songs"), h("ul", { class: "list" }, likes.map((t) => trackRow(t, "liked"))));
  out.append(h("h3", {}, "Followed artists"), h("ul", { class: "list" }, lib.artists.map(artistRow)));
  out.append(h("h3", {}, "Saved albums"), h("ul", { class: "list" }, lib.albums.map(albumRow)));
  out.append(h("h3", {}, "Recently played (Jump back in)"), h("ul", { class: "list" }, recent.map((r) => h("li", {}, h("div", { class: "grow" }, h("b", {}, r.name), h("span", { class: "muted" }, `${r.context} · ${ago(r.last_played)}`))))));
  out.append(h("h3", {}, "Recent searches ", h("button", { onclick: async () => { await api("/api/me/searches", { method: "DELETE" }); loadLibrary(); } }, "Clear")),
    h("p", {}, searches.map((s) => h("span", { class: "pill" }, s.query))));
}
$("#lib-refresh").addEventListener("click", loadLibrary);
$("#new-playlist").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/playlists", { method: "POST", body: { name: new FormData(e.target).get("name") } });
  e.target.reset();
  loadLibrary();  // also refreshes state.playlists
});

async function openPlaylist(id) {
  const p = await api(`/api/playlists/${id}`).catch(() => null);
  if (!p) return;
  const reload = () => openPlaylist(id);
  const offline = h("button", { onclick: async () => { const on = p.offline_devices.includes(state.device); await api(`/api/me/downloads/${id}?device=${state.device}`, { method: on ? "DELETE" : "PUT" }); reload(); } },
    p.offline_devices.includes(state.device) ? "Offline on this device ✓" : "Keep offline on this device");
  const cover = h("input", { type: "file", accept: "image/*" });
  cover.addEventListener("change", async () => { const fd = new FormData(); fd.append("file", cover.files[0]); await api(`/api/playlists/${id}/cover`, { method: "PUT", form: fd }); reload(); });
  const ids = p.items.map((i) => i.item_id);
  showDetail(h("h3", {}, p.name), h("p", { class: "muted" }, `${p.track_count} tracks · ${p.cover_url ? "has cover" : "no cover"}`),
    h("div", { class: "row" }, offline, h("button", { onclick: async () => { await api(`/api/playlists/${id}`, { method: "PATCH", body: { pinned_to_sidebar: !p.pinned_to_sidebar } }); reload(); } }, p.pinned_to_sidebar ? "Unpin" : "Pin"), h("label", {}, "Cover ", cover)),
    h("ul", { class: "list" }, p.items.map((it, i) => trackRow(it.track, `playlist:${id}`, h("span", {},
      h("button", { title: "Move up", disabled: i === 0, onclick: async () => { await api(`/api/playlists/${id}/tracks`, { method: "PATCH", body: { item_id: it.item_id, before_item_id: ids[i - 1] } }); reload(); } }, "↑"),
      h("button", { title: "Move down", disabled: i === ids.length - 1, onclick: async () => { await api(`/api/playlists/${id}/tracks`, { method: "PATCH", body: { item_id: it.item_id, before_item_id: ids[i + 2] ?? null } }); reload(); } }, "↓"),
      h("button", { title: "Remove", onclick: async () => { await api(`/api/playlists/${id}/tracks`, { method: "DELETE", body: { item_ids: [it.item_id] } }); reload(); } }, "✕"))))));
}

// ---------------------------------------------------------------- home
async function loadHome() {
  const out = clear($("#home"));
  const feed = await api("/api/home").catch(() => null);
  if (!feed) return;
  for (const s of feed.sections) {
    out.append(h("h3", {}, s.title));
    const tracks = s.items.filter((i) => i.cache_state);
    if (tracks.length) out.append(h("ul", { class: "list" }, tracks.map((t) => trackRow(t, "search"))));
    else out.append(h("ul", { class: "list" }, s.items.map((i) => h("li", {}, h("div", { class: "grow" }, h("b", {}, i.name), h("span", { class: "muted" }, i.context || ""))))));
  }
}
$("#home-refresh").addEventListener("click", loadHome);

// ---------------------------------------------------------------- stats and wrapped
$("#wrapped-year").value = new Date().getFullYear();
$("#stats-load").addEventListener("click", async () => {
  const out = clear($("#stats"));
  const s = await api(`/api/me/stats?range=${$("#stats-range").value}`).catch(() => null);
  if (s) {
    out.append(h("h3", {}, `Stats · ${s.range}`), kv({ "counted plays": s.plays, skips: s.skips, "minutes listened": s.minutes_listened, "days listened": s.days_listened }));
    out.append(h("h4", {}, "Top tracks"), h("ul", { class: "list" }, s.top_tracks.map((t) => h("li", {}, h("div", { class: "grow" }, h("b", {}, t.track.title), h("span", { class: "muted" }, names(t.track.artists))), h("span", { class: "pill" }, `${t.plays} plays · ${t.minutes} min`)))));
    out.append(h("h4", {}, "Top artists"), h("ul", { class: "list" }, s.top_artists.map((a) => h("li", {}, h("div", { class: "grow" }, h("b", {}, a.artist.name)), h("span", { class: "pill" }, `${a.plays} plays · ${a.minutes} min`)))));
  }
  const w = await api(`/api/me/wrapped?year=${$("#wrapped-year").value}`).catch(() => null);
  if (w) {
    add(out, h("h3", {}, `Wrapped ${w.year}`), w.cards.length ? null : h("p", { class: "muted" }, "No plays that year."));
    for (const c of w.cards) {
      const scalars = Object.fromEntries(Object.entries(c).filter(([k, v]) => !["id", "title", "items", "track", "artist", "album"].includes(k) && typeof v !== "object"));
      out.append(h("div", { class: "card" }, h("h4", {}, c.title), kv({ ...(c.track && { song: c.track.title }), ...(c.artist && { artist: c.artist.name }), ...(c.album && { album: c.album.title }), ...scalars }),
        c.items ? h("ol", {}, c.items.map((i) => h("li", {}, `${(i.track || i.artist).title || (i.track || i.artist).name} — ${i.plays} plays`))) : null));
    }
  }
});
const kv = (o) => h("dl", { class: "kv" }, Object.entries(o).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));

// ---------------------------------------------------------------- import
let importTimer = null;
async function startImport(post) {
  clearInterval(importTimer);
  let r;
  try { r = await post(); } catch { return; }
  const bar = h("progress", { max: 1, value: 0 }), line = h("p", {}, "Starting…"), result = h("div", {});
  clear($("#import-status")).append(h("p", {}, `Import #${r.import_id} → playlist #${r.playlist_id}`), bar, line, result);
  state.importUi = { id: r.import_id, bar, line };
  const poll = async () => {
    const s = await api(`/api/imports/${r.import_id}`, { quiet: true }).catch(() => null);
    if (!s) return;
    bar.max = s.total || 1; bar.value = s.matched + s.unmatched.length;
    line.textContent = `${s.status}: ${s.matched} matched, ${s.unmatched.length} unmatched of ${s.total}${s.error ? ` — ${s.error}` : ""}`;
    if (s.status !== "running") {
      clearInterval(importTimer);
      add(clear(result), h("button", { onclick: () => { showTab("library"); setTimeout(() => openPlaylist(r.playlist_id), 400); } }, "Open the playlist"),
        s.unmatched.length ? h("ul", { class: "list" }, s.unmatched.map((u) => h("li", {}, h("div", { class: "grow" }, h("b", {}, u.title || "(no title)"), h("span", { class: "muted" }, `row ${u.row} · ${u.artist} · ${u.reason}`))))) : null);
      refreshPlaylists();
    }
  };
  importTimer = setInterval(poll, 1000);
  poll();
}
$("#import-url").addEventListener("submit", (e) => { e.preventDefault(); const fd = new FormData(e.target); startImport(() => api("/api/import", { method: "POST", body: { url: fd.get("url"), ...(fd.get("name") ? { name: fd.get("name") } : {}) } })); });
$("#import-csv").addEventListener("submit", (e) => { e.preventDefault(); startImport(() => api("/api/import", { method: "POST", form: new FormData(e.target) })); });

// ---------------------------------------------------------------- websocket: events, devices, transfer
function send(msg) { if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(msg)); }
function sendPlayerState() {
  if (!state.current) return;
  send({ type: "player_state", track_id: state.current.track.id, position_ms: Math.round(audio.currentTime * 1000), playing: !audio.paused, at: Date.now() });
}
["play", "pause", "seeked"].forEach((ev) => audio.addEventListener(ev, sendPlayerState));
audio.addEventListener("ended", () => reportPlay(false));
setInterval(() => { if (!audio.paused) sendPlayerState(); }, 15000);

function connect() {
  clearTimeout(state.wsTimer);
  if (state.ws) { state.ws.onclose = null; state.ws.close(); }
  if (!state.pid) return;
  const ws = (state.ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?profile=${state.pid}&device=${state.device}&name=${encodeURIComponent(`Test console ${state.device.slice(0, 4)}`)}`));
  const dot = (on, text) => { $("#ws-dot").classList.toggle("on", on); $("#ws-text").textContent = text; };
  ws.onopen = () => { state.wsDelay = 1000; dot(true, "connected"); state.ping = setInterval(() => send({ type: "ping" }), 25000); };
  ws.onclose = () => { clearInterval(state.ping); dot(false, "reconnecting…"); state.wsTimer = setTimeout(connect, state.wsDelay); state.wsDelay = Math.min(state.wsDelay * 2, 30000); };
  ws.onmessage = (e) => onSocket(JSON.parse(e.data));
}
async function onSocket(m) {
  if (m.type !== "pong" && m.type !== "download_progress") {
    const el = $("#events");
    el.textContent = `${new Date().toLocaleTimeString()}  ${JSON.stringify(m).slice(0, 400)}\n` + el.textContent.split("\n").slice(0, 80).join("\n");
  }
  switch (m.type) {
    case "track_cached": setLive(m.video_id, { state: "cached" }); break;
    case "download_progress": if (state.live[m.video_id]?.state !== "cached") setLive(m.video_id, { state: "downloading", percent: m.percent }); break;
    case "download_failed": setLive(m.video_id, { state: "failed" }); toast(`Download failed: ${m.error}`, true); break;
    case "import_progress": if (state.importUi?.id === m.import_id) { state.importUi.bar.max = m.total || 1; state.importUi.bar.value = m.done; } break;
    case "state": state.serverState = m; $("#server-state").textContent = json(m.state); renderDevices(m.devices, m.active_device_id); break;
    case "devices": renderDevices(m.list); break;
    case "queue_update": $("#server-state").textContent = json(m.state); break;
    case "active_device":
      if (m.device_id !== state.device && !audio.paused) { audio.pause(); toast("Playback moved to another device"); }
      if (m.device_id === state.device && m.resume && m.state?.current) resumeHere(m.state);
      break;
  }
}
async function resumeHere(st) {
  const t = await api(`/api/tracks/${st.current.track_id}`).catch(() => null);
  if (!t) return;
  state.current = { track: t, context: "queue" };
  audio.src = `/api/stream/${t.video_id}?profile=${state.pid}`;
  $("#now").textContent = `${t.title} — ${names(t.artists)}`;
  audio.addEventListener("loadedmetadata", () => { audio.currentTime = (st.position_ms || 0) / 1000; audio.play(); }, { once: true });
}
function renderDevices(list) {
  $("#my-device").textContent = state.device.slice(0, 8);
  clear($("#devices")).append(h("ul", { class: "list" }, list.map((d) => h("li", {},
    h("div", { class: "grow" }, h("b", {}, `${d.name}${d.id === state.device ? " (this browser)" : ""}`), h("span", { class: "muted" }, `${d.online ? "online" : "offline"} · last seen ${ago(d.last_seen)}`)),
    d.active ? h("span", { class: "pill" }, "active") : null,
    h("button", { disabled: !d.online || d.id === state.device && d.active, onclick: () => { sendPlayerState(); send({ type: "transfer", to_device: d.id }); } }, "Transfer playback here")))));
}
$("#clear-events").addEventListener("click", () => { $("#events").textContent = ""; });

// ---------------------------------------------------------------- admin
async function loadAdmin() {
  const out = clear($("#admin"));
  const [s, cfg] = await Promise.all([api("/api/admin/status"), api("/api/settings")]).catch(() => []);
  if (!s) return;
  const mb = (n) => `${(n / 1048576).toFixed(1)} MB`;
  out.append(h("h3", {}, "Downloads"), kv({ ...s.jobs, "active high": s.active_by_priority.high, "active low": s.active_by_priority.low }));
  out.append(h("h3", {}, "Disk"), kv({ used: mb(s.disk.used_bytes), limit: mb(s.disk.limit_bytes), pinned: mb(s.disk.pinned_bytes), files: s.disk.file_count, "over budget": String(s.disk.over_budget), codec: cfg.codec }));
  out.append(h("h3", {}, `Failed downloads (${s.failed.length})`), h("ul", { class: "list" }, s.failed.map((f) => h("li", {},
    h("div", { class: "grow" }, h("b", {}, f.track ? `${f.track.title} — ${f.track.artist}` : f.video_id), h("span", { class: "muted" }, `${f.attempts} attempts · ${f.error || ""}`)),
    h("button", { onclick: async () => { await api(`/api/admin/jobs/${f.video_id}/retry`, { method: "POST" }); loadAdmin(); } }, "Retry"),
    h("button", { disabled: !f.track, onclick: async () => { await api(`/api/admin/jobs/${f.video_id}/alternative`, { method: "POST" }); loadAdmin(); } }, "Try next-best source")))));
  out.append(h("h3", {}, "yt-dlp"), h("p", {}, `Version ${s.yt_dlp_version} `, h("button", { onclick: async () => {
    if (!confirm("Update yt-dlp and restart the API and worker? The API will be unavailable for a few seconds.")) return;
    const r = await api("/api/admin/ytdlp/update", { method: "POST" }).catch(() => null);
    if (r) toast(`yt-dlp ${r.from} → ${r.to}. Restarting…`);
  } }, "Update yt-dlp")));
}
$("#admin-refresh").addEventListener("click", loadAdmin);

// ---------------------------------------------------------------- raw API explorer
$("#explorer-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target), out = $("#explorer-out");
  let body;
  if (fd.get("body").trim()) { try { body = JSON.parse(fd.get("body")); } catch (err) { out.textContent = `Body is not valid JSON: ${err.message}`; return; } }
  const t0 = performance.now();
  const r = await fetch(fd.get("path"), { method: fd.get("method"), headers: { ...(state.pid ? { "X-Profile-Id": state.pid } : {}), ...(body !== undefined ? { "Content-Type": "application/json" } : {}) }, body: body !== undefined ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let shown = text;
  try { shown = json(JSON.parse(text)); } catch { /* not JSON (audio, image…) */ }
  out.textContent = `${r.status} ${r.statusText} · ${Math.round(performance.now() - t0)} ms · ${r.headers.get("content-type") || ""}\n\n${shown.slice(0, 20000)}`;
  log(`${fd.get("method")} ${fd.get("path")} → ${r.status}`);
});

loadProfiles().catch((e) => toast(`Could not reach the API: ${e.message}`, true));
