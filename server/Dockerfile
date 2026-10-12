FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# yt-dlp needs a JS runtime for YouTube challenges
COPY --from=denoland/deno:bin-2.9.6 /deno /usr/local/bin/deno
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

RUN useradd --create-home app
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir /opt/venv && chown app /opt/venv
USER app
COPY --chown=app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=app . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
