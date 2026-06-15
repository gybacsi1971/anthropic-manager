#!/usr/bin/env bash
# PROD frissítés a Docker-hoston: git pull → újraépítés → indítás.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Hiányzik a .env fájl a PROD szerveren. L. docs/installation.md."
  exit 1
fi

echo "==> git pull"
git pull --ff-only

echo "==> docker compose up -d --build"
docker compose up -d --build

echo "==> állapot"
docker compose ps
echo "Kész."
