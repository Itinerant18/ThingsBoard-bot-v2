FROM ghcr.io/astral-sh/uv:latest AS uv

# Build the chat widget in its own stage so node never reaches the runtime image —
# the app serves static files and needs no JS toolchain at run time. Copying the
# manifests before the sources keeps `npm ci` cached across source-only edits.
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
RUN useradd --create-home --uid 10001 appuser
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev
COPY app ./app
# Operational scripts (backfill, extraction, seeding) run inside the container via
# `docker exec ... python -m scripts.<name>`, so they have to ship with the image.
COPY scripts ./scripts
# Only the build output; app/main.py mounts /ui from frontend/dist.
COPY --from=frontend /frontend/dist ./frontend/dist
COPY alembic.ini ./
RUN chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8083
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
