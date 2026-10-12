# Build context is the repository root (see infra/docker/docker-compose.yml) so the image can
# install the local packages/analytics domain package alongside the API.
FROM python:3.12-slim

WORKDIR /workspace/apps/api

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY packages/analytics /workspace/packages/analytics
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app

# Two steps: the API resolves analytics by path, then the editable install replaces it so
# mounted source edits to packages/analytics are picked up without a rebuild.
RUN pip install --no-cache-dir -e ".[dev]" \
    && pip install --no-cache-dir -e /workspace/packages/analytics

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
