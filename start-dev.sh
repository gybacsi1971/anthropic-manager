#!/usr/bin/env bash
# Lokális fejlesztői indítás (Docker Compose, dev profil).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Hiányzik a .env fájl. Másold a .env.example-t .env-be és töltsd ki:"
  echo "  cp .env.example .env"
  echo "Generálj Fernet kulcsot:"
  echo "  openssl rand -base64 32 | tr '+/' '-_'"
  exit 1
fi

docker compose -f docker-compose.dev.yaml up -d --build
echo "Elindult — nyisd meg: http://localhost:8010"
echo "Logok:  docker compose -f docker-compose.dev.yaml logs -f app"
