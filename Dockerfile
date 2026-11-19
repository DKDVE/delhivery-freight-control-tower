# Freight Control Tower: FastAPI serves the API and the built React SPA (frontend/dist) as one service, one container.
# Build:  docker build -t fct-app .
# Run:    docker run -p 8000:8000 -e DEMO_MODE=1 fct-app

# ---------------------------------------------------------------- builder: install Python deps into a prefix
FROM python:3.10-slim AS builder
WORKDIR /build
COPY requirements.txt .
# shap/__init__.py imports shap.utils._clustering, which imports numba at module level, so numba/llvmlite are a
# genuine hard dependency of `import shap` in this version (0.49.1) - not just an unused explainer path as a first
# check seemed to show (that check's `pip uninstall` had silently no-op'd). Keep them; only __pycache__ is stripped.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && find /install -type d -name "__pycache__" -exec rm -rf {} +

# ---------------------------------------------------------------- runtime
FROM python:3.10-slim AS runtime

# libgomp1: lightgbm's OpenMP runtime. No compiler, no dev headers, no apt cache left behind.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 1000 appuser
WORKDIR /app

# Only what app/tools.py, backend/ and the built SPA need at runtime - never data/raw, .env, .venv, notebooks or tests.
COPY backend/ backend/
COPY app/ app/
COPY src/ src/
COPY prompts/ prompts/
COPY models/ models/
COPY data/processed/ data/processed/
COPY reports/metrics/ reports/metrics/
COPY reports/figures/ reports/figures/
COPY frontend/dist/ frontend/dist/

# AUDIT_LOG_DIR defaults to app/logs; give it a writable home before dropping root, in case no volume is mounted.
# chown only this small directory, not the whole tree (COPY already leaves everything world-readable, and a
# recursive chown over the ~120 MB of models/data here would double the image size on overlay filesystems).
RUN mkdir -p /app/app/logs && chown -R appuser:appuser /app/app/logs
USER appuser

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
# No DEMO_MODE/AUDIT_LOG_DIR default here: app/llm.py already falls back to DEMO_MODE when LLM_API_KEY is unset,
# and app/guardrails.py already defaults AUDIT_LOG_DIR to app/logs (= /app/app/logs in this image). The cloud sets
# both explicitly (infra/main.bicep); `docker run -e DEMO_MODE=1` is only needed to force demo mode with a key set.

EXPOSE 8000

# No curl in the image: a one-line Python request keeps the runtime stage minimal.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
