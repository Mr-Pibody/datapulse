# syntax=docker/dockerfile:1

# --- Stage 1 : Builder ---
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install ".[api,parquet,excel]"


# --- Stage 2 : Runtime ---
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATAPULSE_DB_PATH="/app/db/datapulse.sqlite"

RUN groupadd --gid 10001 datapulse && \
    useradd --uid 10001 --gid datapulse --shell /bin/bash --create-home datapulse

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY configs ./configs
COPY data/raw ./data/raw
COPY data/reference ./data/reference

RUN mkdir -p /app/db /app/reports /app/data/processed /app/data/inbox /app/data/archive && \
    chown -R datapulse:datapulse /app

USER datapulse

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["datapulse", "serve", "--host", "0.0.0.0", "--port", "8000", "--db", "/app/db/datapulse.sqlite"]