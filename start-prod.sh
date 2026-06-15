#!/usr/bin/env bash
# PROD indítás a Traefik-es szerveren (docker-compose.yml). Frissítéshez l. prod-rebuild.sh.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Hiányzik a .env fájl. L. docs/installation.md."
  exit 1
fi

docker compose up -d --build
docker compose ps
echo "Elindult."
