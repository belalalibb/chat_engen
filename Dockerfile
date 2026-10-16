# syntax=docker/dockerfile:1.6
# chat_engine — multi-stage, non-root. Upstream general_ai_core is pinned by SHA in pyproject.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui_standalone/package.json ui_standalone/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY ui_standalone/ ./
RUN npx vite build

FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY chat_engine ./chat_engine
COPY compat ./compat
RUN python -m venv /venv && /venv/bin/pip install --upgrade pip wheel \
 && /venv/bin/pip wheel --wheel-dir /wheels . \
 && /venv/bin/pip install --no-index --find-links /wheels chat_engine || /venv/bin/pip install .

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" PORT=8080 CE_UI_DIST=/app/ui_dist
RUN useradd -r -u 10001 -m ce
WORKDIR /app
COPY --from=build /venv /venv
COPY --from=build /app/compat /app/compat
COPY --from=ui /ui/dist /app/ui_dist
USER ce
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"
CMD ["python", "-m", "chat_engine", "serve"]
