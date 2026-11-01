# Python 3.11 Production Dockerfile for Video Downloader & OSINT (Robin + Blackbird)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Install OS dependencies: ffmpeg for yt-dlp & audio, git, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements
COPY requirements.txt .

# Install dependencies including pinned Werkzeug==2.2.2 for Blackbird url_quote compatibility
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir Werkzeug==2.2.2

# Copy application source code and submodules
COPY . .

# Create temporary downloads and logs directories
RUN mkdir -p downloads_temp logs blackbird/results && \
    chmod -R 777 downloads_temp logs blackbird/results 2>/dev/null || true

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
