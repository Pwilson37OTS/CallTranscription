# Multi-stage build:
# Stage 1 — pull static ffmpeg binaries from a dedicated image.
# Stage 2 — build the final runtime on python:3.12-slim, copying the static
# binaries across and installing the (small) remaining packages via apt.
#
# Why multi-stage: Railway's builder appears to mount /var/cache/apt/archives
# as a very small tmpfs (<20 MB). ffmpeg pulls in ~241 dependencies (~143 MB
# of .deb files) which overflows that mount. Grabbing ffmpeg as a static
# binary from a different image bypasses apt for the heavyweight and lets
# the remaining apt installs fit comfortably.

FROM mwader/static-ffmpeg:7.0 AS ffmpeg

FROM python:3.12-slim

# Cache bust: 2026-05-20-v2

# Static ffmpeg + ffprobe (no apt, no dependencies)
COPY --from=ffmpeg /ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg /ffprobe /usr/local/bin/ffprobe

# Redirect apt's archive cache to /tmp before any apt operation. /tmp on
# Railway's builder has more headroom than /var/cache. Then install the
# small remaining packages (supervisor + nginx + curl ≈ 14 MB).
RUN mkdir -p /tmp/apt-cache/partial && \
    echo 'Dir::Cache::Archives "/tmp/apt-cache";' > /etc/apt/apt.conf.d/99-tmp-archives && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        supervisor \
        nginx \
        curl \
    && rm -rf /var/lib/apt/lists/* /tmp/apt-cache

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/uploads data/converted data/logs data/cloudcall_downloads

EXPOSE 8080

HEALTHCHECK CMD curl --fail http://localhost:8080/health || exit 1

ENTRYPOINT ["supervisord", "-c", "supervisord.conf"]
