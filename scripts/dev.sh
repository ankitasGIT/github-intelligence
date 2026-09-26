#!/usr/bin/env bash
# Bring up the local stack (Postgres + Redis + API) via Docker Compose.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill in GITHUB_TOKEN before Phase 2."
fi

docker compose -f infra/docker/docker-compose.yml up --build
