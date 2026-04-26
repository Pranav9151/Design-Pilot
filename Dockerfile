# ══════════════════════════════════════════════════════════════════════
# DesignPilot MECH — Production Dockerfile
#
# Multi-stage build:
#   stage 1 (builder)  — install Python deps into a venv
#   stage 2 (frontend) — build Vite bundle
#   stage 3 (runtime)  — minimal Python image + compiled assets
#
# Build:
#   docker build -t designpilot-mech:latest .
#
# Run:
#   docker run -p 8000:8000 --env-file .env designpilot-mech:latest
# ══════════════════════════════════════════════════════════════════════

# ── Stage 1: Python dependency build ─────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System packages needed to compile some wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ".[dev]" 2>&1 | tail -3

# ── Stage 2: Frontend build ───────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /fe

COPY frontend/package*.json ./
RUN npm ci --silent

COPY frontend/ ./

# Build args for the frontend (passed at build time)
ARG VITE_SUPABASE_URL=""
ARG VITE_SUPABASE_ANON_KEY=""
ENV VITE_SUPABASE_URL=$VITE_SUPABASE_URL
ENV VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY

RUN npm run build

# ── Stage 3: Runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="waqar@designpilot.in"
LABEL org.opencontainers.image.title="DesignPilot MECH"
LABEL org.opencontainers.image.description="AI-powered mechanical engineering design API"

# Runtime system packages only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    # Create a non-root application user
    && groupadd --gid 1001 dpmech \
    && useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/false dpmech

WORKDIR /app

# Copy compiled venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py

# Copy compiled frontend assets
COPY --from=frontend-builder /fe/dist ./frontend/dist

# Writable runtime directory (used by sandbox + local storage fallback)
RUN mkdir -p /app/.runtime && chown dpmech:dpmech /app/.runtime

USER dpmech

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

EXPOSE 8000

CMD ["sh", "-c", \
    "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http h11"]
