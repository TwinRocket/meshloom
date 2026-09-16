# Stage 1: Build frontend
# Pinned to the machine doing the building, not the machine being built for. The
# frontend stage emits static files, so it has no reason to run under emulation —
# and node publishes no armv7 image at all, so building for a Raspberry Pi 3 fails
# outright without this. It also drops the emulated npm build from every arm64
# release, which was pure cost.
FROM --platform=$BUILDPLATFORM node:24-slim AS frontend-builder

ARG COMMIT_HASH=unknown

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci

COPY frontend/ ./
RUN VITE_COMMIT_HASH=${COMMIT_HASH} npm run build


# Stage 2: the dependency manifests, with this project's own version taken out.
#
# A release changes one line in pyproject.toml and one in uv.lock — its version —
# and nothing else. Copied as they are, that line is part of the cache key of the
# dependency install below, so the release was the one build guaranteed to
# recompile eight C extensions under emulation for armv7. Thirty-one minutes,
# measured on 4.10.0, for a string the layer does not use.
#
# Normalised here instead. `COPY --from` is keyed on what the files contain, so
# two releases hand that layer the same bytes. This stage runs on the build
# machine and costs a second.
FROM --platform=$BUILDPLATFORM python:3.14-slim AS deps-manifest

WORKDIR /manifest

COPY pyproject.toml uv.lock ./
COPY scripts/build/neutralize_project_version.py ./
RUN python neutralize_project_version.py .


# Stage 3: Python runtime
FROM python:3.14-slim

WORKDIR /app

# Install uv from PyPI rather than from its published image: that image exists for
# amd64 and arm64 only, so a `COPY --from` cannot build for armv7 — which is what
# a Raspberry Pi 3 running Home Assistant is. The wheel exists for every
# architecture built here, and the version is the same one the image carried.
ARG UV_VERSION=0.6.17
RUN pip install --no-cache-dir "uv==${UV_VERSION}"


# Copy dependency files first for layer caching, version-neutral so that a
# release does not invalidate what follows.
COPY --from=deps-manifest /manifest/pyproject.toml /manifest/uv.lock ./

# Install dependencies (no dev/test deps).
#
# The toolchain is installed and removed inside one layer because armv7 has no
# published wheel for several dependencies — httptools and cffi compile from
# source there — and the slim image ships neither a compiler nor their headers. On amd64 and arm64 the wheels exist and
# nothing is built, so this costs those images a little time and no size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && uv sync --frozen --no-dev \
    && apt-get purge -y --auto-remove build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# The real manifests, once nothing expensive depends on them. The running app
# reads its version from pyproject.toml, and 0.0.0 is not the version it shipped.
COPY pyproject.toml uv.lock ./

# Copy application code
COPY app/ ./app/

# Layer-2 bundled hashtag names: same CC0 snapshot the backend loads
# (repo path first, then this packaged copy next to meshcore_channels.py).
COPY frontend/src/data/meshcoreChannels.snapshot.json ./app/data/meshcoreChannels.snapshot.json

# Copy license attributions
COPY LICENSES.md ./

# Copy built frontend from first stage
COPY --from=frontend-builder /build/dist ./frontend/dist

# Create data directory for SQLite database
RUN mkdir -p /app/data

# Last, because its value changes with every commit and an ENV layer invalidates
# everything built after it. Declared any earlier, it rebuilt the dependency
# layer on every push — which on armv7 means compiling eight C extensions under
# emulation: 21 minutes, measured, for a string only read at runtime.
ARG COMMIT_HASH=unknown
ENV COMMIT_HASH=${COMMIT_HASH}

EXPOSE 8000

# Run the application (we retain root for max compatibility)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
