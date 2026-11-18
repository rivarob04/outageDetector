# syntax=docker/dockerfile:1.7
# ---- UI build ----
FROM --platform=$BUILDPLATFORM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# ---- Python deps ----
FROM python:3.12-slim AS py
ENV PIP_NO_CACHE_DIR=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.9.*
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project --no-cache
COPY backend/pulse ./pulse
RUN uv sync --frozen --no-dev --extra postgres --no-editable --no-cache

# ---- Runtime ----
FROM python:3.12-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="pulse" \
      org.opencontainers.image.description="Self-hosted outage map and down detector" \
      org.opencontainers.image.source="https://github.com/rivarob04/outageDetector" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PULSE_DATA_DIR=/data PULSE_STATIC_DIR=/app/static PULSE_CATALOG_DIR=/app/catalog
RUN groupadd -g 10001 pulse && useradd -u 10001 -g pulse -d /data -s /usr/sbin/nologin pulse \
    && mkdir -p /data && chown pulse:pulse /data
COPY --from=py /opt/venv /opt/venv
COPY --from=ui /ui/dist /app/static
COPY catalog /app/catalog
USER 10001:10001
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["pulse"]
CMD ["serve"]
