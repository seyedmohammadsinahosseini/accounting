# Multi-stage production Dockerfile for Accounting Platform
FROM python:3.11-slim as base

# Set environment flags
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PORT=8000 \
    ACCT_DATA_DIR=/app/data

# Install runtime system packages and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create unprivileged system user for hardened security
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup --home /app appuser

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and entrypoints
COPY app /app/app
COPY pyproject.toml /app/pyproject.toml
COPY LICENSE /app/LICENSE
COPY THIRD-PARTY-NOTICES.md /app/THIRD-PARTY-NOTICES.md
COPY tools.py /app/tools.py
COPY reset_demo.py /app/reset_demo.py
COPY run.sh /app/run.sh

# Initialize required storage directories with secure permissions (0700)
RUN mkdir -p /app/data /app/data/backups /app/data/logs && \
    chown -R appuser:appgroup /app && \
    chmod +x /app/run.sh /app/tools.py

# Switch to non-root execution
USER appuser

EXPOSE 8000

# Container healthcheck monitoring endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

# Production command
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
