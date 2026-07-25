# ── Stage 1: build deps ────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: slim runtime image ────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /install /usr/local

COPY decktrackerAPI/ ./decktrackerAPI/

RUN useradd -m apiuser && chown -R apiuser:apiuser /app
USER apiuser

CMD ["uvicorn", "decktrackerAPI.main:app", "--host", "0.0.0.0", "--port", "8000"]
