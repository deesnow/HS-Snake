# ── Stage 1: build deps ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: slim runtime image ────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime libs for Pillow + gosu for entrypoint privilege drop
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY bot/ ./bot/
COPY assets/ ./assets/

# Non-root user for security (created here; entrypoint drops to this user at runtime)
RUN useradd -m botuser \
    && mkdir /app/data /app/logs \
    && chown botuser:botuser /app/data /app/logs

COPY docker/bot/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
