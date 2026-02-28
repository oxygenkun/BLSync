# ============ Frontend Build Stage ============
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package files first for better cache layering
COPY frontend/package*.json ./

# Install dependencies with cache cleanup
RUN npm ci --prefer-offline --no-audit && \
    npm cache clean --force

# Copy source code and build
COPY frontend/ ./

# Build and cleanup in single layer
RUN npm run build && \
    rm -rf node_modules src public .git *.md .eslintrc.* tsconfig.* vite.config.* tailwind.config.*


# ============ Final Runtime Stage ============
FROM python:3.13-alpine

ENV PATH=/root/.local/bin:$PATH \
    HOME=/app \
    BLSYNC_BASE_DIR=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apk update && \
    apk add --no-cache ffmpeg su-exec

# Copy entrypoint with permissions
COPY --chmod=755 docker/docker-entrypoint.sh /usr/local/bin/

# Copy uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/

# Copy project files (excluding unnecessary files via .dockerignore)
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src/

# Copy built static files from frontend-builder stage
COPY --from=frontend-builder /app/static /app/static

# Install Python dependencies (exclude dev dependencies)
WORKDIR /app
RUN uv sync --locked --no-editable --no-dev && \
    uv cache clean

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD [ "uv", "run", "--no-sync", "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
