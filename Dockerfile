FROM python:3.12-slim

# Cache bust: 2026-05-20

# Install support packages first. Kept in its own RUN so the next RUN
# starts in a fresh container layer with a clean /var/cache/apt/archives/.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        supervisor \
        nginx \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install ffmpeg in its own RUN. ffmpeg pulls in ~241 dependencies and
# ~143 MB of .deb archives. Without a fresh layer here, /var/cache/apt
# in the slim base image can't hold the peak download, and a cache-miss
# build fails. Separate RUN = separate working layer = enough headroom.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

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
