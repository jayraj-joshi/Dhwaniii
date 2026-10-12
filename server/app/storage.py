"""Audio-store paths (D16). Every path kept in the database is relative to AUDIO_DIR."""
import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover

from . import config

log = logging.getLogger("resonance.storage")
GB = 1024**3


def art_rel(kind: str, key) -> str:
    return f"art/{kind}/{key}.jpg"


def audio_rel(video_id: str, ext: str) -> str:
    return f"{video_id[0:2]}/{video_id[2:4]}/{video_id}.{ext}"


def abs_path(rel: str) -> Path:
    return Path(config.AUDIO_DIR) / rel


def resolve(rel: str | None) -> Path | None:
    """Absolute path for a stored relative path, or None if it is empty, escapes AUDIO_DIR or doesn't exist."""
    if not rel:
        return None
    root = Path(config.AUDIO_DIR).resolve()
    p = (root / rel).resolve()
    return p if p.is_relative_to(root) and p.is_file() else None


# --- post-processing (blocking; callers use asyncio.to_thread) ---------------------

def measure_loudness(path: Path) -> tuple[float, float]:
    """(loudness_lufs, gain_db) from ffmpeg loudnorm (D32): -14 LUFS target, true peak never above -1 dBTP."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, check=True)
    j = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S).group(0))
    lufs, tp = float(j["input_i"]), float(j["input_tp"])
    return lufs, max(-20.0, min(6.0, min(-14 - lufs, -1 - tp)))


def crop_art(src: Path, dst: Path) -> None:
    """Center-crop to a square and scale to 600x600 JPEG (D45)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=600:600",
                    "-frames:v", "1", "-q:v", "3", str(dst)], check=True)


def probe_bitrate_kbps(path: Path) -> int | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return round(int(out) / 1000) if out.isdigit() else None


def write_tags(path: Path, title: str, artists: list[str], album: str | None, year: int | None, cover: Path | None) -> None:
    """Clean tags and cover, written once after metadata is resolved (D31)."""
    img = cover.read_bytes() if cover and cover.exists() else None
    if path.suffix == ".m4a":
        f = MP4(path)
        f["\xa9nam"], f["\xa9ART"] = [title], [", ".join(artists)]
        if album:
            f["\xa9alb"] = [album]
        if year:
            f["\xa9day"] = [str(year)]
        if img:
            f["covr"] = [MP4Cover(img, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
    else:
        try:
            tags = ID3(path)
        except Exception:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=", ".join(artists)))
        if album:
            tags.add(TALB(encoding=3, text=album))
        if year:
            tags.add(TDRC(encoding=3, text=str(year)))
        if img:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img))
        tags.save(path)


# --- eviction (D33) ------------------------------------------------------------------

async def storage_used(db) -> int:
    return await db.fetchval("SELECT coalesce(sum(bytes), 0) FROM files")


async def evict(db) -> dict:
    """If files exceed STORAGE_LIMIT_GB, delete unpinned files (not liked by anyone), least recently played first, down to 90 %.
    Track and source rows stay, so an evicted track is simply fetched again when next played."""
    limit = int(config.STORAGE_LIMIT_GB * GB)
    used = await storage_used(db)
    out = {"used": used, "limit": limit, "evicted": 0, "freed": 0, "over_budget": False}
    if used <= limit:
        return out
    target = int(limit * 0.9)
    rows = await db.fetch(
        "SELECT f.source_id, f.path, f.bytes FROM files f JOIN sources s ON s.id = f.source_id "
        "WHERE NOT EXISTS (SELECT 1 FROM likes l WHERE l.track_id = s.track_id) "  # pinned (D17): liked by anyone...
        "AND NOT EXISTS (SELECT 1 FROM playlist_tracks pt JOIN device_downloads dd ON dd.playlist_id = pt.playlist_id WHERE pt.track_id = s.track_id) "  # ...or in a playlist some device keeps offline
        "ORDER BY coalesce(f.last_played, f.created_at)")
    for r in rows:
        if used <= target:
            break
        try:
            abs_path(r["path"]).unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not delete %s: %s", r["path"], e)
            continue
        await db.execute("DELETE FROM files WHERE source_id = $1", r["source_id"])
        used -= r["bytes"]
        out["evicted"] += 1
        out["freed"] += r["bytes"]
    out["used"] = used
    out["over_budget"] = used > limit
    if out["over_budget"]:
        log.warning("storage over budget (%d > %d bytes) and everything left is pinned", used, limit)
    return out
