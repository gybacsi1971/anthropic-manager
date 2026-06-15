# Telepítési útmutató — PROD (Docker + Traefik)

Ez az útmutató az Anthropic Manager PROD telepítését írja le egy Docker-hostra,
**Traefik** reverse proxy mögé, egy dedikált, nem-root Docker-felhasználóval.

## 1. Előfeltételek

- Docker + Docker Compose a hoston.
- Egy futó **Traefik**, amely egy `traefik` nevű **external** Docker hálózatot
  használ, `websecure` entrypoint-tal és `le` (Let's Encrypt) cert-resolverrel.
  (A `docker-compose.yml` labeljei ezeket feltételezik — ha a szerveren más a
  hálózat/entrypoint/resolver neve, igazítsd a labeleket.)
- DNS A-rekord a választott domainre (pl. `anthropic-manager.example.com`),
  amely a szerverre mutat.
- Egy **Anthropic Admin API kulcs** (`sk-ant-admin…`). Csak szervezeti admin
  tudja kiállítani a Console → Settings → Admin keys oldalon. (Egyéni fiókoknál
  az Admin API nem elérhető.)

## 2. Kód és könyvtár

```bash
# Lépj be a dedikált Docker-felhasználóval (a saját neveddel helyettesítve):
sudo -iu <docker-user>
git clone <repo-url> anthropic-manager
cd anthropic-manager
```

## 3. Környezet (.env)

```bash
cp .env.example .env
```

Töltsd ki a `.env`-et:

- `POSTGRES_PASSWORD` — erős, véletlen jelszó. Generálás (URL-biztos):
  ```bash
  openssl rand -hex 24
  ```
- `API_KEY_ENCRYPTION_KEY` — generáld:
  ```bash
  openssl rand -base64 32 | tr '+/' '-_'
  ```
  **Ne változtasd meg később** — a meglévő titkosított kulcsok különben dekódolhatatlanná válnak.
- `TRAEFIK_ROUTER_NAME` — pl. `anthropic-manager`.
- `TRAEFIK_HOST` — a publikus domain, pl. `anthropic-manager.example.com`.
- `HOST_UID` / `HOST_GID` — a dedikált Docker-felhasználó azonosítói:
  ```bash
  id -u <docker-user>    # HOST_UID
  id -g <docker-user>    # HOST_GID
  ```
- `ENV_TYPE` / `ENV_COLOR` — opcionális badge (pl. `PROD` / `#C81E1E`).

## 4. Indítás

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

A Traefik automatikusan kiállítja a TLS tanúsítványt. Nyisd meg a domaint:
`https://<TRAEFIK_HOST>`.

## 5. Első beállítás

1. A `/setup` oldalon hozd létre az első **admin** felhasználót.
2. **Admin kulcsok** oldal → *Új kulcs* → írd be a címkét és az `sk-ant-admin…`
   kulcsot → mentés → **Teszt** (a szervezet neve visszajön).
3. **Gyűjtés** oldal → *Szinkronizálás* forrásonként, vagy *Backfill* egy
   múltbeli dátumtartományra (usage/cost 1d granularitás, Claude Code naponta).
4. Ezután a háttér-ütemező a **Beállítások** oldalon megadott intervallumokkal
   automatikusan frissít.

## 6. Frissítés

```bash
./prod-rebuild.sh      # git pull --ff-only → docker compose up -d --build
```

## 7. Adatmentés és visszaállítás

Az adatok a `pgdata` Docker named volume-ban élnek.

```bash
# Mentés
docker compose exec -T db pg_dump -U anthropic_manager anthropic_manager | gzip > backup_$(date +%F).sql.gz

# Visszaállítás (üres DB-be)
gunzip -c backup_YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U anthropic_manager anthropic_manager
```

A gyűjtött adat bármikor újraépíthető az Admin API-ból (backfill), így a mentés
elsősorban a felhasználók, beállítások és a feldolgozott előzmény megőrzéséről szól.

## 8. Hibakeresés

- **Compose hibával áll meg induláskor** → hiányzó kötelező `.env` változó
  (a `:?` jelzi, melyik). Töltsd ki.
- **„Nincs aktív Admin API kulcs”** → vegyél fel és aktiválj egyet az Admin kulcsok oldalon.
- **A sync hibára fut** → a **Gyűjtés** oldal *Futások előzménye* táblája és a
  konténer logja (`docker compose logs app`) mutatja az Admin API hibaüzenetét
  (pl. 401 = rossz kulcs, 403 = nincs admin jogosultság).
- **TLS nem áll fel** → ellenőrizd a DNS-t, a `traefik` external hálózatot és a
  `le` cert-resolver nevét a Traefik konfigjában.
