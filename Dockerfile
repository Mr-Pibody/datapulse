FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs

RUN pip install --upgrade pip && \
    pip install ".[api,parquet,excel]" && \
    useradd --create-home --shell /bin/bash datapulse && \
    mkdir -p /app/db /app/reports /app/data/raw /app/data/processed && \
    chown -R datapulse:datapulse /app

USER datapulse

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["datapulse", "serve", "--host", "0.0.0.0", "--port", "8000"]