# Közreműködés — Anthropic Manager

Köszönjük, hogy hozzá szeretnél járulni! Az alábbiak a projekt fejlesztési
konvencióit foglalják össze.

## Fejlesztői környezet

Előfeltétel: Docker + Docker Compose.

```bash
cp .env.example .env
# Fernet kulcs az API_KEY_ENCRYPTION_KEY-be:
openssl rand -base64 32 | tr '+/' '-_'
# POSTGRES_PASSWORD (URL-biztos):
openssl rand -hex 24

./start-dev.sh        # docker compose -f docker-compose.dev.yaml up -d --build
# Leállítás: ./stop-dev.sh   (a DB adat megmarad; -v kapcsolóval a volument is törli)
```

Az app a <http://localhost:8010> címen érhető el; első indításkor a `/setup`
varázsló hozza létre az admin felhasználót.

## Munkafolyamat

1. Forkold a repót, és hozz létre egy témára szabott branchet
   (pl. `feat/cache-bontas`, `fix/cost-idosor`).
2. Tartsd a változtatást fókuszáltan; egy PR egy logikai egységet old meg.
3. Nyiss Pull Requestet érthető leírással (mit, miért, hogyan tesztelhető).

## Commit-konvenció

A projekt **Conventional Commits**-ot használ, magyar üzenettel:

```
feat(cost): becsült költség-megoszlás API kulcs szerint
fix(ui): jobb felső felhasználói menü javítása
chore: dev/prod start-stop scriptek
```

Típusok: `feat`, `fix`, `chore`, `refactor`, `docs`, `perf`, `test`.

## Kód-elvek (kötelező)

- **NINCS FALLBACK.** Hiányzó kötelező env változó → azonnali hiba; tilos
  `os.environ.get(X) or default` jellegű csendes alapérték.
- **Magyar** elnevezések, kommentek és UI.
- Nincs ORM: nyers `psycopg2`, paraméteres lekérdezésekkel (SQL injection nélkül).
- Új API-végpont: `routes_*.py`, `/api/...` prefix, `auth.get_current_user/admin`
  guard, mutáló műveletnél `activity_logger.log_activity`.
- A frontend külső függőség nélküli (self-hosted assetek a `frontend/vendor/`-ban);
  ne vezess be új CDN-hivatkozást.
- Titkot **soha** ne commitolj — használd a `.env`-et (gitignore-olt) és a
  `.env.example`-t sablonként.

## Ellenőrzés PR előtt

```bash
python3 -m py_compile backend/*.py     # szintaxis-ellenőrzés deps nélkül
```

A részletes architektúra és modul-leírás a [CLAUDE.md](CLAUDE.md)-ben található.
