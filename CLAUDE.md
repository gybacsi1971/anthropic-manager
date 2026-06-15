# CLAUDE.md — Anthropic Manager fejlesztői útmutató

Önállóan hostolt Anthropic használat/költség konzol. Az Anthropic Admin API-ból
gyűjt adatot helyi PostgreSQL-be, és testreszabható elemzéseket jelenít meg.

## Architektúra

```
Admin API ──(httpx, sk-ant-admin)──► collector (idempotens upsert) ──► PostgreSQL
                         ▲ scheduler (asyncio, forrásonkénti intervallum)
Frontend (vanilla JS + Chart.js) ──► /api/... (FastAPI, csak a helyi DB-t kérdezi)
```

## Stack és elvek

- **FastAPI + nyers psycopg2 (nincs ORM)**, PostgreSQL 16, httpx, Fernet, Chart.js
  (self-hosted: `frontend/vendor/chart.js/`, nincs build). Az Inter + Material Icons
  fontok szintén self-hosted (`frontend/vendor/fonts/`) — nincs külső CDN, a CSP tiszta `self`.
- **NINCS FALLBACK**: hiányzó kötelező env (`DATABASE_URL`, `API_KEY_ENCRYPTION_KEY`)
  → azonnali `RuntimeError`. Tilos `os.environ.get(X) or default`.
- **Magyar elnevezések, kommentek, UI.**
- Az Admin API kulcs (`sk-ant-admin…`) Fernet-titkosítva a `admin_api_keys` táblában;
  a teljes érték SOSEM hagyja el a backendet.

## Adatforrások (Admin API)

| Forrás | Végpont | Megjegyzés |
|---|---|---|
| Usage | `GET /v1/organizations/usage_report/messages` | `group_by[]`, `bucket_width` 1d/1h/1m, page-token lapozás |
| Cost | `GET /v1/organizations/cost_report` | `bucket_width` csak 1d, amount **centben** |
| Claude Code | `GET /v1/organizations/usage_report/claude_code` | egyetlen nap (`YYYY-MM-DD`), page-token |
| Metaadat | `workspaces` / `api_keys` / `users` / `me` | cursor (after_id) lapozás |

Közös fejlécek: `x-api-key`, `anthropic-version: 2023-06-01`. Frissesség: usage/cost ~5 perc,
Claude Code ~1 óra. Polling fenntarthatóan percenként egyszer.

**Mai nap kezelése (fontos):** az Usage API a folyamatban lévő mai napot **1d**
bontásban NEM adja vissza (csak lezárt napokat), de **1h**-ban igen. Ezért a
`collector.collect_usage_today()` a mai napot órás bontásban kéri le, napi szintre
aggregálja, és egyetlen `1d` sorként (dim-enként) upsert-eli — másnap a valódi napi
bucket ugyanazzal a `dim_hash`-sel felülírja (nincs dupla számolás, mert órás sort
nem tárolunk). A **Cost API kizárólag 1d** és a mai napot szintén nem adja → a mai
költség csak a nap lezárultával érhető el (API-korlát, a UI jelzi).

## Backend modulok

- `config.py` — env (fallback nélkül), API-konstansok, verzió.
- `database.py` — pool, `PgConnection`, `get_db()`, `advisory_lock()`, `init_database()` (séma + seed).
- `auth.py` — PBKDF2, session, HttpOnly cookie, `get_current_user/admin` dependency-k. Szerepkör: `admin` | `viewer`.
- `admin_key_service.py` — Fernet kulcs-kezelés, `get_active_key()`.
- `anthropic_admin_client.py` — httpx kliens, lapozás (page-token / cursor), 429/5xx backoff.
- `collector.py` — normalizálás + **idempotens upsert** (`dim_hash`), `run_sync()` (advisory lock + `sync_runs` audit, dátum-chunkolás).
- `scheduler.py` — asyncio ciklus, forrásonkénti intervallum a `settings`-ből.
- `pricing_service.py` — modell-árazás (becsült költség): `model_pricing` CRUD + a hivatalos
  pricing oldal letöltése és parse-olása (stdlib `HTMLParser`, nincs új függőség).
- `routes_*.py` — auth, admin_keys, sync, usage, cost, claude_code, metadata, settings, pricing, balance, activity.
- `main.py` — app, lifespan (init + ütemező), oldal-route-ok, statikus kiszolgálás.

## Becsült költség (árlista alapján)

A Cost API nem adja a mai/folyó napot és a priority tier költségét. Ezért a **Költség**
oldal a tényleges költség mellé **becsültet** is mutat: a `usage_facts` token-darabszámait
megszorozza a szerkeszthető **`model_pricing`** árlistával (USD/MTok). Egy adott napra a
tényleges adat az irányadó, ha a Cost API már lezárta (van `cost_facts` sor); ahol nincs
(pl. a mai nap), a becsült érték tölti ki — a diagramon a modell színének **40%-os**
változatával + „becsült" felirattal (`/api/cost/combined-timeseries`).

- **Illesztés:** `model_pricing.model_pattern` a `usage_facts.model` **előtag-illesztése**,
  a SQL a **leghosszabb** illeszkedő mintát választja (`starts_with` + `ORDER BY length DESC`),
  így a dátum-utótagos id-k is illeszkednek (pl. `claude-haiku-4-5-20251001` → `claude-haiku-4-5`).
- **NINCS csendes 0:** az árazatlan modellek nem $0-ként számítanak, hanem külön
  `unpriced_models` figyelmeztetésként jelennek meg (NO FALLBACK elv).
- **Árlista oldal** (`/pricing`, admin): szerkeszthető rács + „Frissítés a hivatalos
  árlistából" gomb, ami a docs oldalt parse-olja és a rácsba tölti (review-then-apply, nem ment).
- A `model_pricing` seed és a `pricing.web_search_usd_per_request` settings kulcs az init-ben
  `ON CONFLICT DO NOTHING` → újrainduláskor **nem írja felül** az admin szerkesztéseit.
- A becslő-SQL (`EST_USD_EXPR`, `PRICE_JOIN_LATERAL`, `WS_PRICE_SETTING`) a `pricing_service.py`-ban
  él, és a `routes_cost` (idősor) + `routes_balance` (egyenleg) közösen használja (egy forrás).

## Szervezet egyenlege (kézi horgony, pontos időpont)

Az **Admin API NEM ad kredit-egyenleget** (csak a Console → Billing oldal mutatja). Ezért az
egyenleg **kézi horgony**: az admin beír egy egyenleget egy **pontos időponttal**
(`balance.anchor_usd` + `balance.anchor_ts` UTC ISO a `settings`-ben; a frontend `toISOString()`-zal
küldi a helyi `datetime-local` értéket), és az app a horgony **időpontjától** vonja le a felmerült költséget:
- a horgony **napján** csak a horgony utáni órák **becsült** költségét (`usage_hourly_facts`),
- a horgony napja utáni **lezárt** napok **tényleges** költségét (`cost_facts`),
- a nyitott teljes napok (pl. ma) **becsült** költségét (`usage_facts` 1d).

A `usage_hourly_facts` egy külön tábla: a collector a mai órás lekérést **modellenként** is eltárolja
(a többi 1d lekérdezést nem érinti, nincs dupla számolás; néhány naposnál régebbi sorokat töröl). Ha a
horgony napjára nincs órás adat (régi horgony), a nap költségét **időarányosan** becsüli
(`anchor_day_prorated`). Áttekintő tetején nagy kijelző (`/api/balance`, admin). Becslés — a Console
valós számával időnként újraszinkronizálandó (a priority tier költségét a Cost API nem adja).

## Cache-hatékonyság (költségoptimalizálás)

A prompt-cache token-adatok megvannak (`usage_facts`: `cache_read_input_tokens`,
`cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `uncached_input_tokens`). Ebből:
- **Költség oldal** (`/api/cost/cache-savings`): találati arány, cache-olvasás-megtakarítás
  (a read a beviteli ár 10%-áért megy → 90% megtakarítás), cache-írás-felár (+25% / +100%),
  **nettó cache-haszon** (USD). A token-arányok a teljes forgalomra, a USD az árazott modellekre.
- **Használat oldal** (`/api/usage/cache-breakdown`): modellenkénti cache-token bontás + találati arány (token-szemlélet).

## Idempotencia (fontos)

Minden ténytáblához **`dim_hash`** (md5 a dimenziók kanonikus konkatenációjából),
és `UNIQUE (bucket_start, bucket_width, dim_hash)`. Az `INSERT … ON CONFLICT DO UPDATE`
a friss bucket-eket felülírja. NULL-os dimenziók: a hash mindig azonos sentinel-t ad,
így nincs duplikáció (Postgres-ben NULL≠NULL egyedi indexben).

## Ütemező

`uvicorn --workers 1` → egyetlen ütemező-példány. A collector Postgres advisory lock-ja
akkor is véd az átfedéstől (manuális + ütemezett, vagy több worker esetén).

## Futtatás

```bash
./start-dev.sh   # dev: docker compose -f docker-compose.dev.yaml up -d --build
./stop-dev.sh    # dev leállítás (a DB adat megmarad; -v: a volument is törli)
# Prod: ./start-prod.sh / ./stop-prod.sh (docker-compose.yml); frissítés: ./prod-rebuild.sh
```

Szintaxis-ellenőrzés deps nélkül: `python3 -m py_compile backend/*.py`.

## Konvenciók

- Új API-végpont: `routes_*.py`, `/api/...` prefix, `auth.get_current_user/admin` guard, `activity_logger.log_activity` a mutáló műveletekhez.
- Frontend: a központi `api.js` kliens, `app-shell.js` bootstrap, `charts.js` diagramok. Új oldal = HTML (shell konténerek + `<meta name="page-title">`) + `window.pageInit` a saját JS-ben.
- Plan mode-ban a TODO fájlok a projekt `TODOs/` mappájába kerülnek (elkészülve `TODOs/_archived/`).
