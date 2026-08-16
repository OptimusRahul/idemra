FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir uv && uv sync --no-dev

ENTRYPOINT ["uv", "run", "idemra"]
