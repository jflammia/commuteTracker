FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output for logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Version injected at build time by CI (from git tag)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Install dependencies first (better layer caching - only rebuilds when deps change)
COPY pyproject.toml .
RUN pip install --no-cache-dir . && \
    rm -rf /root/.cache

# Copy application code
COPY src/ src/
COPY scripts/ scripts/
COPY zones.json.example zones.json.example

# Create non-root user
RUN groupadd --gid 1000 commute && \
    useradd --uid 1000 --gid commute --shell /bin/bash commute && \
    mkdir -p /data && chown commute:commute /data

USER commute

# Default environment
ENV DATABASE_URL=sqlite:////data/commute_tracker.db \
    RECEIVER_HOST=0.0.0.0 \
    RECEIVER_PORT=8080

EXPOSE 8080

VOLUME ["/data"]

# OCI image labels (populated by docker/metadata-action in CI)
LABEL org.opencontainers.image.title="Commute Tracker" \
      org.opencontainers.image.description="Self-hosted GPS commute analytics with automatic transport mode detection" \
      org.opencontainers.image.url="https://github.com/jflammia/commuteTracker" \
      org.opencontainers.image.source="https://github.com/jflammia/commuteTracker" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="MIT"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["uvicorn", "src.receiver.app:app", "--host", "0.0.0.0", "--port", "8080"]
