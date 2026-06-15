#!/usr/bin/env bash
# Lokális dev leállítás (docker-compose.dev.yaml). Az adat (pgdata_dev volume) megmarad.
# Használat: ./stop-dev.sh [-v|--purge]   (-v: a DB volument is törli)
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "-v" ] || [ "${1:-}" = "--purge" ]; then
  echo "FIGYELEM: a pgdata_dev volument (DB tartalom) is törlöm."
  docker compose -f docker-compose.dev.yaml down -v
else
  docker compose -f docker-compose.dev.yaml down
fi
echo "Leállítva."
