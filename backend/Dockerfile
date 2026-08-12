# Cicero API — self-hosted image (A6 / NFR-O-2).
#
# Runs as a non-root user, carries no secrets (everything comes from the
# environment at run time — NFR-SEC-1/3), and writes its SQLite database to
# /data so the container filesystem stays disposable.
#
# Built with *this directory* as context:
#   docker build -f Dockerfile .        # from backend/
# (docker-compose.yml already does this.)
#
# It used to be built from the repository root, because pyproject.toml declared
# `readme = "../README.md"` and that file had to be reachable. That field is
# gone — newer hatchling rejects a readme outside the project directory — so the
# context no longer has to straddle the whole repo.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY cicero ./cicero

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
