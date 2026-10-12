"""Text normalization and title parsing (planning.md §9.1, §9.2)."""
import re
import unicodedata

NOISE_PHRASES = sorted(
    ["official music video", "official video", "official audio", "lyric video", "lyrical video",
     "video song", "full song", "full video", "lyrics", "lyric", "audio", "hd", "hq", "4k"],
    key=len, reverse=True,
)
_NOISE_RE = re.compile(r"(?<!\S)(?:%s)(?!\S)" % "|".join(map(re.escape, NOISE_PHRASES)))


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not 0x300 <= ord(c) <= 0x36F)
    s = unicodedata.normalize("NFC", s)
    s = s.replace("'", "").replace("’", "").replace("&", " and ")
    s = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in s)
    return " ".join(s.split())


def normalize_query(q: str) -> str:
    n = normalize(q)
    return " ".join(_NOISE_RE.sub(" ", n).split()) or n


# --- parse_title -----------------------------------------------------------

_TITLE_NOISE = r"official|video|audio|lyrics?|hd|hq|4k|visualizer|full song"
_BRACKET_NOISE = re.compile(rf"\s*[\(\[][^\)\]]*\b(?:{_TITLE_NOISE})\b[^\)\]]*[\)\]]", re.I)
_FEAT_BRACKET = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+?)\s*[\)\]]", re.I)
_FEAT_BARE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")
_PIPE = re.compile(r"\s+\|\s+")
_NOISE_WORDS = set(re.findall(r"[a-z0-9]+", _TITLE_NOISE))
_CHANNEL_SUFFIX = re.compile(r"\s*(?:-\s*Topic|VEVO|Official)\s*$", re.I)


def _names(s: str) -> list[str]:
    return [n.strip() for n in s.split(",") if n.strip()]


def _is_noise(seg: str) -> bool:
    words = re.findall(r"[a-z0-9]+", seg.lower())
    return not words or all(w in _NOISE_WORDS for w in words)


def _split_feat(s: str) -> tuple[str, list[str]]:
    featured: list[str] = []
    for rx in (_FEAT_BRACKET, _FEAT_BARE):
        m = rx.search(s)
        if m:
            featured += _names(m.group(1))
            s = s[:m.start()] + s[m.end():]
    return s.strip(), featured


def _result(title, artists, featured=(), album=None, year=None, stage="title"):
    """`stage` says where the data came from: hint, fields and topic are authoritative music metadata, title is a guess."""
    return {"title": title, "artists": list(artists), "featured": list(featured), "album": album or None, "year": year, "stage": stage}


def _from_topic_description(info: dict):
    lines = [l.strip() for l in (info.get("description") or "").splitlines()]
    if "Provided to YouTube by" not in " ".join(lines):
        return None
    i = next(i for i, l in enumerate(lines) if l.startswith("Provided to YouTube by"))
    rest = [l for l in lines[i + 1:] if l]
    if not rest or " · " not in rest[0]:
        return None
    title, *artists = [p.strip() for p in rest[0].split(" · ")]
    album = rest[1] if len(rest) > 1 and not rest[1].startswith(("Released on", "℗")) else None
    m = re.search(r"Released on:\s*(\d{4})", "\n".join(lines))
    return _result(title, artists, album=album, year=int(m.group(1)) if m else None, stage="topic")


def _from_title(info: dict):
    raw = info.get("title") or ""
    cleaned = _BRACKET_NOISE.sub("", raw).strip() or raw.strip()
    parts = [p.strip() for p in _DASH.split(cleaned) if p.strip()]
    album = None
    if len(parts) >= 2:
        artist_part, title_part, *tail = parts
        # a pipe tail may sit inside the title segment: "Tum Hi Ho | Aashiqui 2 | HD"
        pipes = [p.strip() for p in _PIPE.split(title_part) if p.strip()]
        title_part, tail = pipes[0], pipes[1:] + tail
        artist_part, featured = _split_feat(artist_part)
        title_part, f2 = _split_feat(title_part)
        artists = _names(artist_part)
        featured += f2
    else:
        # ponytail: pipe-only titles are read as "title | album | ..." (artist comes from the channel)
        pipes = [p.strip() for p in _PIPE.split(cleaned) if p.strip()] or [cleaned]
        title_part, tail = pipes[0], pipes[1:]
        title_part, featured = _split_feat(title_part)
        channel = _CHANNEL_SUFFIX.sub("", info.get("channel") or info.get("uploader") or "").strip()
        artists = [channel] if channel else []
    album = next((t for t in tail if not _is_noise(t)), None)
    return _result(title_part or cleaned, artists, featured, album)


def parse_title(info: dict) -> dict:
    """info is a yt-dlp info dict or a TrackHint. Returns {title, artists, featured, album, year, stage}."""
    # 1. TrackHint from ytmusicapi: artists are dicts
    arts = info.get("artists")
    if arts and isinstance(arts[0], dict):
        alb = info.get("album")
        return _result(info["title"], [a["name"] for a in arts], album=alb.get("title") if isinstance(alb, dict) else alb, stage="hint")
    # 2. yt-dlp music fields
    if info.get("track") and (arts or info.get("artist")):
        names = arts if arts else _names(info["artist"])
        return _result(info["track"], names, album=info.get("album"), year=info.get("release_year"), stage="fields")
    # 3. Topic description, then 4. rule-based fallback
    return _from_topic_description(info) or _from_title(info)
