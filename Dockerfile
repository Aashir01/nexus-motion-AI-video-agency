# ── build the dashboard ───────────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── python dependencies ───────────────────────────────────────────────────
FROM python:3.11-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt

# ── runtime ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# ffmpeg is not optional here: it is the render engine.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_LOCAL_ROOT=/data/storage

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
COPY nexus/ ./nexus/
COPY migrations/ ./migrations/
COPY alembic.ini pyproject.toml ./
COPY --from=frontend /ui/dist ./static/

RUN useradd -u 10001 -m nexus \
    && mkdir -p /data/storage \
    && chown -R nexus:nexus /app /data
USER nexus

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "nexus.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
