# Anthropic Manager

Önállóan hostolt **Anthropic használati és költség konzol**. Az Anthropic
**Admin API**-n keresztül gyűjti a szervezet adatait (token-felhasználás,
USD költség, Claude Code analitika, metaadatok), helyi PostgreSQL-be tárolja,
és testreszabható dashboardokon jeleníti meg — a `console.anthropic.com`
Usage/Cost oldalainál mélyebb, saját elemzésekkel.

## Mit tud

- **Háttér-ütemező** periodikusan, idempotensen gyűjti az Admin API riportjait
  a helyi DB-be (gördülő ablak + igény szerinti backfill).
- **Használat** (tokenek): bontás modell / workspace / API-kulcs / service tier /
  context window szerint; cache-hatékonyság.
- **Költség** (USD): napi trend, megoszlás workspace / modell / költségtípus szerint.
- **Claude Code analitika**: fejlesztői rangsor, eszköz-elfogadási arányok, LOC,
  commit/PR, becsült költség.
- **Admin metaadatok**: workspace-ek, API-kulcsok, tagok (ID → név a riportokban).
- Több felhasználó, szerepkörökkel (admin / néző), audit napló.

## Stack

- **Backend:** FastAPI (Python 3.12), nyers psycopg2 + PostgreSQL 16 (nincs ORM),
  httpx (Admin API), Fernet (kulcs-titkosítás), asyncio ütemező.
- **Frontend:** vanilla HTML/CSS/JS (nincs build), Chart.js + Inter/Material Icons
  fontok **self-hosted** (`frontend/vendor/`, nincs külső CDN).
- **Deploy:** Docker Compose + Traefik (tetszőleges Docker-host, dedikált nem-root user).

## Gyors indítás (lokális dev)

```bash
cp .env.example .env
# Generálj Fernet kulcsot és írd az API_KEY_ENCRYPTION_KEY-be:
openssl rand -base64 32 | tr '+/' '-_'
# Állíts be egy POSTGRES_PASSWORD-öt is (URL-biztos):
openssl rand -hex 24

./start-dev.sh      # vagy: docker compose -f docker-compose.dev.yaml up -d --build
# Leállítás:  ./stop-dev.sh   (a DB adat megmarad; -v kapcsolóval a volument is törli)
```

Nyisd meg: <http://localhost:8010> → első indításkor a **setup** varázsló
létrehozza az első admin felhasználót.

Ezután: **Admin kulcsok** oldal → vegyél fel egy `sk-ant-admin…` kulcsot →
**Teszt** → **Gyűjtés** oldal → *Szinkronizálás* vagy *Backfill*.

## Telepítés PROD-ba (Docker + Traefik)

Lásd: [docs/installation.md](docs/installation.md).

## Projektstruktúra

```
backend/    FastAPI app, gyűjtő, ütemező, Admin API kliens, route-ok
frontend/   vanilla HTML/CSS/JS oldalak + Chart.js
docs/       telepítési útmutató
```

## Fontos elvek

- **Nincs fallback**: hiányzó kötelező env változó → azonnali hiba.
- Az Admin API kulcs **nem** env-ben, hanem Fernet-titkosítva a DB-ben él.
- A gyűjtés **idempotens** (`dim_hash` + `ON CONFLICT`), a backfill nem duplikál.

## Közreműködés

Lásd: [CONTRIBUTING.md](CONTRIBUTING.md).

## Licenc

[MIT](LICENSE).

## Megjegyzés a böngésző-konzolhoz

Ha a DevTools konzoljában `contentscript.js`, `ObjectMultiplex`,
`MaxListenersExceededWarning` vagy „message channel closed before a response was
received" üzeneteket látsz, azok **böngésző-kiterjesztésekből** (pl. crypto-wallet)
származnak, nem az alkalmazásból — az app maga nem tölt külső erőforrást, és a
Content-Security-Policy tiszta `self`.
