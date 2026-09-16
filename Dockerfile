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

# Which commit this is. Resolved in a stage of its own so that .git, and the
# history it carries, never lands in a layer of the image that ships.
FROM python:3.11-slim AS commit
WORKDIR /src
COPY .git .git
COPY backend/intake/buildinfo.py ./
RUN python buildinfo.py --git-dir .git --out SOURCE

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

# What this image is and where it came from. Decisions are written to the
# database during the build above, so `docker compose up` without --build serves
# the old wording from the old image and looks like a code change that did not
# take. The fingerprint says whether the image is the same one; the commit says
# whether it is the source you meant, which is the half you can act on.
#
# Last, so that a new commit does not invalidate the dependency install or the
# pipeline run above it.
COPY --from=commit /src/SOURCE ./SOURCE
RUN python -m intake.buildinfo --root . --source SOURCE --out BUILD_STAMP \
 && rm SOURCE

EXPOSE 8000
CMD ["uvicorn", "intake.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
