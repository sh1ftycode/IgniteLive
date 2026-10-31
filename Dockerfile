# Ignite backend. The desktop app is not part of this image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IGNITE_DATA=/data

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]" \
    && useradd --system --uid 10001 --home-dir /data ignite \
    && install -d -o ignite -m 700 /data

USER ignite
VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/v1/health', timeout=3)"

CMD ["ignite-server", "serve", "--host", "0.0.0.0", "--port", "8787"]
