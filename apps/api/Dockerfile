# Build context: repository root.  docker build -f apps/api/Dockerfile .
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
COPY apps/worker ./apps/worker
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 formwork && mkdir -p /app/.formwork && chown formwork /app/.formwork
USER formwork
EXPOSE 8000
CMD ["uvicorn", "formwork_api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
