FROM python:3.12-alpine

# install tools
RUN apk update && apk add --no-cache ffmpeg
RUN python -m pip install uv
RUN uv tool install yutto

# copy files
COPY pyproject.toml requirements.lock README.md config src /app/

# install dependencies and project
WORKDIR /app
# RUN uv pip install --no-cache -r requirements.lock --system
RUN --mount=source=dist,target=/dist uv pip install --no-cache /dist/*.whl --system
RUN uv cache clean

CMD [ "bs", "-c", "/app/config/config.toml" ]

EXPOSE 8000
