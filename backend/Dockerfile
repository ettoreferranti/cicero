# Cicero API — self-hosted image (A6 / NFR-O-2).
#
# Runs as a non-root user, carries no secrets (everything comes from the
# environment at run time — NFR-SEC-1/3), and writes its SQLite database to
# /data so the container filesystem stays disposable.
#
# Built from the *repository root* as context, because backend/pyproject.toml
# declares `readme = "../README.md"` — that file has to be reachable:
#   docker build -f backend/Dockerfile .
# (docker-compose.yml already does this.)
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pyproject sits at /app, so its `../README.md` resolves to /README.md.
COPY README.md /README.md
COPY backend/pyproject.toml ./
COPY backend/cicero ./cicero

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 cicero \
    && mkdir -p /data \
    && chown -R cicero:cicero /data

USER cicero

ENV DATABASE_URL=sqlite:////data/cicero.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Inside the container we must listen on all interfaces; the compose file is
# what keeps the port published to localhost only.
CMD ["uvicorn", "cicero.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
