#!/usr/bin/env bash
# PROD leállítás (docker-compose.yml). Az adat (named volume) megmarad.
# Használat: ./stop-prod.sh [-v|--purge]   (-v: a DB volument is törli)
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "-v" ] || [ "${1:-}" = "--purge" ]; then
  echo "FIGYELEM: a DB volument (adatbázis tartalom) is törlöm."
  docker compose down -v
else
  docker compose down
fi
echo "Leállítva."
