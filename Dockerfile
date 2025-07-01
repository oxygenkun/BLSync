FROM python:3.12-alpine

ARG UID=1000
ARG GID=1000

ENV UV_COMPILE_BYTECODE=1 \
    PATH=/root/.local/bin:$PATH

# install tools
RUN apk update && apk add --no-cache ffmpeg
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# copy files
COPY pyproject.toml uv.lock README.md /app/
COPY config /app/config/
COPY src /app/src/

# install dependencies and project
WORKDIR /app
RUN uv sync --locked --no-editable \
    && uv tool install yutto \
    && uv cache clean

CMD [ "uv", "run", "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
