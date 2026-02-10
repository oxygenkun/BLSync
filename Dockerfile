FROM python:3.13-alpine

ENV PATH=/root/.local/bin:$PATH \
    HOME=/app

# install tools
RUN apk update && apk add --no-cache ffmpeg su-exec

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /uvx /bin/

# copy files
COPY pyproject.toml uv.lock README.md static/index.html /app/
COPY src /app/src/

# install dependencies and project
WORKDIR /app
RUN uv sync --locked --no-editable \
    && uv cache clean

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD [ "uv", "run", "--no-sync", "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
