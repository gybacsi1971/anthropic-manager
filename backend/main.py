"""
Anthropic Manager — FastAPI alkalmazás belépési pont.

- Lifespan: DB séma inicializálás, félbeszakadt sync-ek lezárása, ütemező indítás.
- API route-ok bekötése (/api/...).
- Statikus frontend kiszolgálása (vanilla HTML/CSS/JS, nincs build).
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import admin_key_service
import auth
import scheduler
from config import APP_VERSION, ENV_TYPE, ENV_COLOR
from database import init_database, close_pool, get_db, now_iso

import routes_auth
import routes_admin_keys
import routes_sync
import routes_usage
import routes_cost
import routes_claude_code
import routes_metadata
import routes_settings
import routes_pricing
import routes_balance
import routes_activity


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    logger.info("Adatbázis séma kész (v%s)", APP_VERSION)
    # Alapértelmezett admin felhasználó env-ből (csak üres DB-nél, első induláskor).
    try:
        if auth.ensure_default_admin():
            logger.info("Alapértelmezett admin felhasználó létrehozva (ADMIN_EMAIL)")
    except ValueError as e:
        logger.warning("Alapértelmezett admin nem jött létre: %s", e)
    # Kényelmi import: ANTHROPIC_ADMIN_KEY[_N] env → DB (Fernet, idempotens label-alapon).
    imported = admin_key_service.import_from_env_once()
    if imported:
        logger.info("%d Admin API kulcs importálva környezeti változóból", imported)
    # Egy újraindítás után nincs aktív futás — a 'running' sorokat lezárjuk.
    with get_db() as con:
        con.execute(
            "UPDATE sync_runs SET status='error', error='megszakítva (újraindítás)', finished_at=%s WHERE status='running'",
            (now_iso(),),
        )
    scheduler.start_scheduler()
    try:
        yield
    finally:
        scheduler.stop_scheduler()
        close_pool()


app = FastAPI(
    title="Anthropic Manager",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


# Content-Security-Policy — minden eszköz saját kiszolgálású (self-hosted):
# Chart.js és a fontok (Inter + Material Icons) a /vendor alól mennek, nincs külső CDN.
# Így a policy tiszta 'self'; a scripteknél NINCS 'unsafe-inline' (nincs inline
# <script>/handler), a style 'unsafe-inline' az inline style="" attribútumok miatt kell.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Biztonsági válasz-fejlécek MINDEN válaszra (proxy-független, mindig jelen van)."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = _CSP
        return response


app.add_middleware(SecurityHeadersMiddleware)

# API route-ok
app.include_router(routes_auth.router)
app.include_router(routes_admin_keys.router)
app.include_router(routes_sync.router)
app.include_router(routes_usage.router)
app.include_router(routes_cost.router)
app.include_router(routes_claude_code.router)
app.include_router(routes_metadata.router)
app.include_router(routes_settings.router)
app.include_router(routes_pricing.router)
app.include_router(routes_balance.router)
app.include_router(routes_activity.router)


VERSIONINFO = Path(__file__).resolve().parent.parent / "VERSIONINFO"


@app.get("/api/version")
def version():
    return {"version": APP_VERSION, "env_type": ENV_TYPE, "env_color": ENV_COLOR}


@app.get("/api/version-history")
def version_history():
    """Verzió-történet a VERSIONINFO mappából.

    Minden fájl: <verzió>.md — 1. sor a dátum, a többi sor a markdown üzenet.
    Szemantikus verzió szerint csökkenő sorrendben. Publikus (mint a /api/version).
    Üres lista, ha nincs .md fájl — ez érvényes „nincs előzmény" adatállapot,
    nem rejtett config-default (a NO-FALLBACK elv a kötelező env/config hiányára vonatkozik).
    """
    if not VERSIONINFO.exists():
        return []
    entries = []
    for f in VERSIONINFO.glob("*.md"):
        content = f.read_text(encoding="utf-8").strip()
        lines = content.split("\n", 1)
        entries.append({
            "version": f.stem,
            "date": lines[0].strip() if lines else "",
            "message": lines[1].strip() if len(lines) > 1 else "",
        })

    def _semver_key(entry):
        try:
            return tuple(int(p) for p in entry["version"].split("."))
        except ValueError:
            return (0, 0, 0)

    entries.sort(key=_semver_key, reverse=True)
    return entries


# ---- Statikus eszközök ----
app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="js")
# Self-hosted függőségek (Chart.js, Inter + Material Icons fontok) — nincs külső CDN.
app.mount("/vendor", StaticFiles(directory=FRONTEND / "vendor"), name="vendor")


# ---- Oldalak (tiszta URL-ek) ----
_PAGES = {
    "/": "index.html",
    "/login": "login.html",
    "/setup": "setup.html",
    "/usage": "usage.html",
    "/cost": "cost.html",
    "/claude-code": "claude-code.html",
    "/admin-keys": "admin-keys.html",
    "/sync": "sync.html",
    "/pricing": "pricing.html",
    "/settings": "settings.html",
    "/users": "users.html",
    "/activity-log": "activity-log.html",
}


def _make_page_route(filename: str):
    def _route():
        return FileResponse(FRONTEND / filename)
    return _route


for _path, _file in _PAGES.items():
    app.add_api_route(_path, _make_page_route(_file), methods=["GET"], include_in_schema=False)
