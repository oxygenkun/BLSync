# ============ Frontend Build Stage ============
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend files
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ============ Final Runtime Stage ============
FROM python:3.13-alpine

ENV PATH=/root/.local/bin:$PATH \
    HOME=/app \
    BLSYNC_BASE_DIR=/app

# install tools
RUN apk update && apk add --no-cache ffmpeg su-exec

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/

# copy files
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src/

# Copy built static files from frontend-builder stage
COPY --from=frontend-builder /app/static /app/static

# install dependencies and project
WORKDIR /app
RUN uv sync --locked --no-editable \
    && uv cache clean

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD [ "uv", "run", "--no-sync", "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
