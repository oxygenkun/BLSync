FROM python:3.13-alpine

ARG UID=1000
ARG GID=1000

ENV UV_COMPILE_BYTECODE=1 \
    PATH=/root/.local/bin:$PATH

# install tools
RUN apk update && apk add --no-cache ffmpeg

# create user with specified UID/GID
RUN addgroup -g ${GID} appuser && \
    adduser -u ${UID} -G appuser -D -s /bin/sh appuser
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/

# copy files
COPY pyproject.toml uv.lock README.md /app/
COPY src /app/src/

# install dependencies and project
WORKDIR /app
RUN uv sync --locked --no-editable \
    && uv tool install yutto \
    && uv cache clean

# change ownership of /app to appuser
RUN chown -R appuser:appuser /app

# switch to non-root user
USER appuser

CMD [ "uv", "run", "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
