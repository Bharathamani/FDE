FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps .

COPY data ./data
COPY config ./config

RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/state \
    && chown -R app:app /app

USER app

ENV COLDCHAIN_DB=/app/state/coldchain.db \
    COLDCHAIN_DATASET=/app/data/sensor_readings.json \
    COLDCHAIN_SEED_DEMO=true \
    COLDCHAIN_DEMO_MODE=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["python", "-m", "coldchain", "serve", "--host", "0.0.0.0", "--port", "8000"]
