# One image, one port, one command. The corpus is generated and the inbox is
# processed at build time, so the container starts with the demo already sitting
# in a known state -- and because both are deterministic, that state is the same
# on every build.

FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Where the corpus, the response cache and the built front end live, stated
# rather than inferred from where the package happens to be installed.
ENV INTAKE_ROOT=/app

COPY pyproject.toml ./
COPY backend/ backend/
RUN pip install --no-cache-dir -e . && rm -rf /root/.cache/pip

# The seeded corpus, the naturalised prose, and the cached model responses. The
# cache is what lets the container run the real pipeline with no API key and no
# network: every call replays from disk.
COPY data/ data/
COPY --from=ui /ui/dist frontend/dist

RUN python -m intake.db.seed --db data/intake.sqlite3 \
 && python -m intake.pipeline.process --db data/intake.sqlite3 --provider replay --quiet

EXPOSE 8000
CMD ["uvicorn", "intake.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
