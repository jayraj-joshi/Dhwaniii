"""The only module that reads environment variables (D5)."""
import os
from pathlib import Path

e = os.environ.get
ROOT = Path(__file__).resolve().parents[2]  # repo root on the host; "/" in the container, where paths are absolute


def _path(v: str) -> str:
    """.env holds ./audio and ./cookies/... meaning repo-relative, whatever directory the server starts in."""
    return str(Path(v) if os.path.isabs(v) else ROOT / v)


DATABASE_URL = e("DATABASE_URL", "postgresql://resonance:resonance@localhost:5432/resonance")
REDIS_URL = e("REDIS_URL", "redis://localhost:6379/0")

AUDIO_DIR = _path(e("AUDIO_DIR", "./audio"))
PREF_CODEC = e("PREF_CODEC", "m4a")  # only m4a or mp3
STORAGE_LIMIT_GB = float(e("STORAGE_LIMIT_GB", "50"))

SEARCH_HIT_THRESHOLD = float(e("SEARCH_HIT_THRESHOLD", "0.82"))
SEARCH_CACHE_TTL = int(e("SEARCH_CACHE_TTL", "86400"))
STREAM_URL_TTL = int(e("STREAM_URL_TTL", "3600"))
DOWNLOAD_LOCK_TTL = int(e("DOWNLOAD_LOCK_TTL", "600"))

WORKER_MAX_JOBS = int(e("WORKER_MAX_JOBS", "2"))
DOWNLOAD_RETRIES = int(e("DOWNLOAD_RETRIES", "3"))

# The web app is served from its own subdomain, so a browser only talks to the API if that origin is listed (comma-separated,
# e.g. https://resonance.example.com). Empty = same-origin only (the /console/ test page).
CORS_ORIGINS = [o.strip().rstrip("/") for o in e("CORS_ORIGINS", "").split(",") if o.strip()]

YTDLP_COOKIES_FILE =_path(e("YTDLP_COOKIES_FILE", "./cookies/cookies.txt"))
# When yt-dlp fails (e.g. YouTube's bot check), RapidAPI's "YouTube MP3" (youtube-mp36) makes the MP3 instead. Empty = no fallback.
RAPIDAPI_KEY = e("RAPIDAPI_KEY", "")

# Autoplay (D51): web search for "similar songs", then an LLM reads the results. This method needs both keys.
TAVILY_API_KEY = e("TAVILY_API_KEY", "")
NVIDIA_API_KEY = e("NVIDIA_API_KEY", "")
NVIDIA_MODEL = e("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")
# Or Gemini alone: the model names similar songs from what it knows, no web search. GEMINI_MODEL is comma-separated, newest first:
# when one is busy (503 / 429) the next one is asked.
GEMINI_API_KEY = e("GEMINI_API_KEY", "")
GEMINI_MODEL = e("GEMINI_MODEL", "gemini-3.5-flash-lite,gemini-3.1-flash-lite")


def autoplay_methods() -> list[str]:
    """The autoplay methods whose keys are set, the default first. Each profile picks one in its prefs (`autoplay_method`)."""
    return [m for m, ok in (("gemini", GEMINI_API_KEY), ("tavily", TAVILY_API_KEY and NVIDIA_API_KEY)) if ok]


def autoplay_method(prefs: dict) -> str | None:
    """The profile's chosen method when it is configured, else the default, else None (autoplay can't run)."""
    methods = autoplay_methods()
    return prefs.get("autoplay_method") if prefs.get("autoplay_method") in methods else (methods[0] if methods else None)
