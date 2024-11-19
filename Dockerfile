FROM python:3.12-alpine

RUN python -m pip install uv
RUN uv tool install yutto
RUN --mount=source=dist,target=/dist uv pip install --no-cache /dist/*.wh
RUN uv cache clean
WORKDIR /app
COPY src config.toml ./

EXPOSE 8000

CMD [ "bs", "-c", "config.toml" ]
