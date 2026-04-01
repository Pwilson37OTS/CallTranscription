FROM python:3.12-slim

# Install ffmpeg and supervisor for multi-process management
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg supervisor curl nginx && \
    rm -rf /var/lib/apt/lists/*

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
