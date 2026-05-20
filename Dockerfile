# syntax=docker/dockerfile:1.4
#
# Multi-stage build:
# - Static ffmpeg from a dedicated image (bypasses ~143 MB of apt downloads).
# - BuildKit cache mounts for apt + pip so package archives land on the
#   build host's main disk instead of Railway's tiny tmpfs mounts at
#   /var/cache/apt/archives and /tmp. Without these mounts, even 14 MB of
#   small packages (supervisor, nginx, curl) exceeded the builder's quota.

FROM mwader/static-ffmpeg:7.0 AS ffmpeg

FROM python:3.12-slim

# Cache bust: 2026-05-20-v3

# Static ffmpeg + ffprobe (no apt, no dependencies)
COPY --from=ffmpeg /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

# apt installs using BuildKit cache mounts. The cache mounts are NOT part
# of the final image layer — they're build-time storage on the host disk,
# which is why this works on a builder with tiny tmpfs caches.
#
# The slim image's docker-clean apt config deletes downloaded .debs right
# after install, which would also empty the cache mount. We remove it so
# the cache mount can actually retain packages across builds.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        supervisor \
        nginx \
        curl

WORKDIR /app

# pip cache mount for faster rebuilds
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Application code
COPY . .

# Data directories
RUN mkdir -p data/uploads data/converted data/logs data/cloudcall_downloads

EXPOSE 8080

HEALTHCHECK CMD curl --fail http://localhost:8080/health || exit 1

ENTRYPOINT ["supervisord", "-c", "supervisord.conf"]
