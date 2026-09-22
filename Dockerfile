# Build the dashboard, then serve it and the API from one origin.
# One origin is also what ANS wants: one hostname is one agent endpoint with one
# certificate.

FROM node:22.19-slim AS ui
WORKDIR /app
COPY package.json package-lock.json ./
COPY ui/package.json ui/
RUN npm ci
COPY ui/ ui/
COPY contracts/ contracts/
RUN npm run build --workspace ui

FROM python:3.13-slim AS runtime
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY server/ server/
COPY agents/ agents/
COPY scripts/ scripts/
COPY contracts/ contracts/
COPY --from=ui /app/ui/dist ui/dist

RUN uv sync --locked --no-dev

# SQLite and live-run artifacts live on the mounted volume, not the image.
ENV DATABASE_PATH=/data/synapse.db
EXPOSE 8080
CMD ["uvicorn", "server.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
