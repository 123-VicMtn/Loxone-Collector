# Image de l'API : waitress, un seul process (le poller et la synchro
# Statistics tournent dedans, voir serve.py). config.yaml, .env et data/
# sont montés par compose.yaml, jamais copiés dans l'image.
#
#   docker build --target test backend   # ruff + pytest en Python 3.12
#   docker compose up -d --build         # image de prod (dernière étape)

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# sqlite3 : sauvegardes (deploy/backup.sh) et requêtes de contrôle.
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-websocket.txt ./
RUN pip install -r requirements.txt -r requirements-websocket.txt
COPY pyproject.toml ./
COPY src ./src
# Versions déjà épinglées par requirements*.txt.
RUN pip install --no-deps .
COPY app.py config.demo.yaml ./
COPY scripts ./scripts
# Lisible par l'utilisateur de l'image, quels que soient les droits des
# fichiers sur la machine de build (certains sont en 600 sur le Mac).
RUN chmod -R a+rX /app


# Validation (pas dans l'image de prod) : le build échoue si ruff ou un
# test échoue.
FROM base AS test
RUN pip install pytest ruff
COPY tests ./tests
RUN ruff check . && python -m pytest -q


FROM base AS prod
RUN useradd --uid 1000 --create-home app && mkdir -p data && chown app:app data
USER app
EXPOSE 8000
# 200 tant que l'API répond, même en "degrade" (un site muet ne se répare
# pas en redémarrant le conteneur).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["python", "-m", "loxone_collector.serve", "config.yaml"]
