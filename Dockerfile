# ── DELULUREEL — Fly.io Dockerfile ──────────────────────────────────────────
# Python 3.11 slim + FFmpeg + librosa/resampy + gunicorn
# Build: fly deploy (triggered by GitHub Actions)
# Region: ams (Amsterdam)

FROM python:3.11-slim

# System deps: FFmpeg, libsndfile (librosa), build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libsndfile1-dev \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verify FFmpeg
RUN ffmpeg -version | head -1

WORKDIR /app

# Copy requirements first — Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create temp dir for video processing
RUN mkdir -p /tmp/delulureel

# Port (Fly.io uses 8080 internally by default)
EXPOSE 8080

# Health check (matches fly.toml + Flask /health endpoint)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Start gunicorn — 2 workers × 4 threads = 8 concurrent requests
# gthread worker class: migliore con I/O bound (fal.ai calls, Supabase)
# max-requests: previene memory leak su processi lunghi
CMD ["gunicorn", "app_server:app", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "300", \
     "--worker-class", "gthread", \
     "--bind", "0.0.0.0:8080", \
     "--max-requests", "500", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
