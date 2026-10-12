"""Updating yt-dlp in place (M4.4-B). YouTube changes break old yt-dlp versions, so the admin can update it without a rebuild:
install the new version into this process's virtualenv, then exit so Docker's `restart: unless-stopped` starts it again on the new one.
The image itself keeps its build-time version, so rebuild it weekly as well (doc §11)."""
import asyncio
import os
import subprocess
import sys

import yt_dlp


def pip_update() -> dict:
    """Blocking. {from, to}. Raises RuntimeError with the tail of uv's output when the install fails."""
    before = yt_dlp.version.__version__
    p = subprocess.run(["uv", "pip", "install", "-U", "--python", sys.executable, "yt-dlp[default]"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[-400:] or "uv pip install failed")
    after = subprocess.run([sys.executable, "-c", "import yt_dlp.version as v; print(v.__version__)"], capture_output=True, text=True).stdout.strip()
    return {"from": before, "to": after or before}


def exit_process() -> None:
    os._exit(0)  # no cleanup on purpose: the container restarts straight away. (On a laptop this stops the dev server.)


async def update_here() -> dict:
    return await asyncio.to_thread(pip_update)


def exit_soon(delay: float = 1.0) -> None:
    """Exit after `delay`, so the HTTP response (or the arq job result) gets out first."""
    asyncio.get_running_loop().call_later(delay, exit_process)
