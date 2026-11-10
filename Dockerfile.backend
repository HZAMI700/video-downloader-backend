# Optimized, Ultra-Slim Python 3.11 Image for Botkeep Cloud (< 300MB)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install only minimal OS dependencies (ffmpeg for media muxing, curl for healthchecks)
# Clean apt caches, doc files, and man pages in the same layer to minimize image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man /var/cache/apt/*

# Copy requirements and install pre-built binary wheels without pip cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache /tmp/* /var/tmp/*

# Copy application source code (filtered via .dockerignore)
COPY . .

# Ensure empty runtime directories exist and clean any legacy temp artifacts
RUN mkdir -p downloads_temp logs blackbird/results && \
    rm -rf downloads_temp/* blackbird/results/* blackbird/logs/* tests blackbird/docs 2>/dev/null || true

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
