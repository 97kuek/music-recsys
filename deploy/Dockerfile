FROM node:22-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY LICENSE NOTICE /build/
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
# 推論のみ。implicit/CUDA・Notebook・学習入力は不要。
RUN uv sync --locked --no-dev --group app --no-install-project
COPY src/ src/
COPY --from=ui /build/src/interfaces/http/static/ src/interfaces/http/static/
RUN uv sync --locked --no-dev --group app && useradd --uid 10001 --create-home music-recsys
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
USER music-recsys
CMD ["python", "-m", "src.interfaces.http.container"]
