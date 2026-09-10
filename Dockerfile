# Paper desk. No MetaTrader in this image.
FROM python:3.12-slim-bookworm

RUN useradd --create-home --uid 10001 bot
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /data && chown -R bot:bot /app /data

USER bot
ENV PYTHONUNBUFFERED=1
WORKDIR /data
# journal and lock live in /data. code is /app.
ENV PYTHONPATH=/app/src

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "from pathlib import Path; p=Path('/data/journal.heartbeat'); raise SystemExit(0 if p.exists() else 1)"

CMD ["python", "-m", "mt5_risk_bot", "run", "--mode", "paper", "--loop"]
