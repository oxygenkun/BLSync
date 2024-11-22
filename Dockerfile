FROM python:3.12-alpine

RUN python -m pip install uv
RUN uv tool install yutto
RUN --mount=source=./,target=/app uv pip install --no-cache -r requirements.lock --system
RUN --mount=source=dist,target=/dist uv pip install --no-cache /dist/*.whl --system
RUN uv cache clean
WORKDIR /app
COPY src config.toml /app/
CMD [ "bs", "-c", "config.toml" ]

EXPOSE 8000
