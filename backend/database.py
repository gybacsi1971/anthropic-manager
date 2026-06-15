"""
PostgreSQL adatbázis-kezelés. Nincs külső ORM — nyers psycopg2.

Mintát a referenciaprojekt (FOR Editor) database.py-ja ad:
  - ThreadedConnectionPool
  - PgConnection vékony wrapper (con.execute(...) kényelmi metódus)
  - get_db() context manager (auto-commit sikerre, rollback hibára)

A séma idempotens (CREATE TABLE IF NOT EXISTS) — biztonságos többszöri futtatás.
"""
import os
from contextlib import contextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
import psycopg2.pool


# ================================================================
# Connection pool
# ================================================================

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=20, dsn=db_url)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


# ================================================================
# PgConnection wrapper + context manager
# ================================================================

class PgConnection:
    """Vékony wrapper: megőrzi a con.execute() kényelmi metódust."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


@contextmanager
def get_db():
    """Kapcsolat context manager-rel; commit sikerre, rollback hibára, putconn finally."""
    pool = _get_pool()
    raw_conn = pool.getconn()
    raw_conn.autocommit = False
    con = PgConnection(raw_conn)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(raw_conn)


# ================================================================
# Postgres advisory lock — folyamatok közti gyűjtés-zár
# ================================================================

# Forrás → lock-azonosító (a sync átfedés-zárhoz). pg_try_advisory_lock kétparaméteres
# formája: (osztály, objektum) — az osztály rögzített, az objektum a forrás.
ADVISORY_LOCK_CLASS = 0x414D  # 'AM' (Anthropic Manager)
SOURCE_LOCK_IDS = {
    "usage": 1,
    "cost": 2,
    "claude_code": 3,
    "metadata": 4,
}


@contextmanager
def advisory_lock(source: str):
    """Session-szintű advisory lock egy adott sync-forráshoz.

    Yield: True, ha megszereztük a zárat; False, ha már fut máshol.
    A zárat egy dedikált kapcsolaton tartjuk a teljes művelet idejére, majd
    feloldjuk. Az upsertek külön get_db() kapcsolatokon mennek.
    """
    if source not in SOURCE_LOCK_IDS:
        raise ValueError(f"Ismeretlen sync-forrás: {source}")
    obj_id = SOURCE_LOCK_IDS[source]
    pool = _get_pool()
    raw = pool.getconn()
    raw.autocommit = True
    acquired = False
    try:
        cur = raw.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (ADVISORY_LOCK_CLASS, obj_id))
        acquired = bool(cur.fetchone()[0])
        yield acquired
    finally:
        if acquired:
            cur = raw.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (ADVISORY_LOCK_CLASS, obj_id))
        pool.putconn(raw)


# ================================================================
# Segédfüggvények
# ================================================================

def now_iso() -> str:
    """UTC időbélyeg ISO-formátumban, 'Z' suffix-szel."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def row_to_dict(row):
    return dict(row) if row is not None else None


def _column_exists(cur, table_name, column_name) -> bool:
    cur.execute(
        """SELECT 1 FROM information_schema.columns
           WHERE table_name = %s AND column_name = %s""",
        (table_name, column_name),
    )
    return cur.fetchone() is not None


# ================================================================
# Séma inicializálás
# ================================================================

# Alapértelmezett globális beállítások — az init seedeli, ha hiányoznak.
# A CLAUDE.md "nincs beégetett default a kódban" elve miatt ezek a DB-ben élnek
# (explicit seed), és a kód a DB-ből olvassa őket — nem env-fallback.
DEFAULT_SETTINGS = {
    "scheduler.enabled": True,
    "scheduler.usage_interval_min": 15,
    "scheduler.cost_interval_min": 15,
    "scheduler.claude_code_interval_min": 60,
    "scheduler.metadata_interval_min": 1440,
    # Hány napra visszamenőleg gyűjtsünk újra minden ütemezett futáskor
    # (az API a friss bucket-eket néhány percig még revideálhatja).
    "scheduler.rolling_window_days": 3,
    # Becsült költséghez: a web keresés ára kérésenként (USD). A hivatalos árlista
    # $10 / 1000 keresés = 0.01 USD/kérés. Az Árjegyzék oldalon szerkeszthető.
    "pricing.web_search_usd_per_request": 0.01,
    # Szervezet egyenlege — KÉZI HORGONY (az Admin API nem ad kredit-egyenleget).
    # Az admin beír egy egyenleget egy PONTOS IDŐPONTTAL (a Console Billingről), és az
    # app a horgony időpontjától vonja le a felmerült költséget — a horgony napján csak
    # az utána eső órákat (usage_hourly_facts), a teljes napokra a napi tény/becslés.
    # Az időpont UTC ISO (a frontend toISOString()-zal küldi). None = nincs beállítva.
    "balance.anchor_usd": None,
    "balance.anchor_ts": None,
}


# Modell-árazás seed a becsült költséghez (USD / 1M token, kivéve ahol jelölve).
# A `model_pattern` a usage_facts.model előtag-illesztéséhez (leghosszabb prefix nyer),
# így a dátum-utótagos id-k (pl. claude-haiku-4-5-20251001) is illeszkednek.
# Forrás: https://platform.claude.com/docs/en/about-claude/pricing — az Árjegyzék
# oldal "Frissítés a hivatalos árlistából" gombja élőben felül tudja írni.
# (pattern, megjelenített név, base input, 5m cache write, 1h cache write, cache read, output, rendezés)
DEFAULT_PRICING = [
    ("claude-opus-4-8",   "Claude Opus 4.8",   5.00,  6.25, 10.00, 0.50, 25.00, 10),
    ("claude-opus-4-7",   "Claude Opus 4.7",   5.00,  6.25, 10.00, 0.50, 25.00, 11),
    ("claude-opus-4-6",   "Claude Opus 4.6",   5.00,  6.25, 10.00, 0.50, 25.00, 12),
    ("claude-opus-4-5",   "Claude Opus 4.5",   5.00,  6.25, 10.00, 0.50, 25.00, 13),
    ("claude-opus-4-1",   "Claude Opus 4.1",  15.00, 18.75, 30.00, 1.50, 75.00, 14),
    ("claude-opus-4",     "Claude Opus 4",    15.00, 18.75, 30.00, 1.50, 75.00, 15),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6", 3.00,  3.75,  6.00, 0.30, 15.00, 20),
    ("claude-sonnet-4-5", "Claude Sonnet 4.5", 3.00,  3.75,  6.00, 0.30, 15.00, 21),
    ("claude-sonnet-4",   "Claude Sonnet 4",   3.00,  3.75,  6.00, 0.30, 15.00, 22),
    ("claude-haiku-4-5",  "Claude Haiku 4.5",  1.00,  1.25,  2.00, 0.10,  5.00, 30),
    ("claude-haiku-3-5",  "Claude Haiku 3.5",  0.80,  1.00,  1.60, 0.08,  4.00, 31),
]


def init_database():
    """Táblák létrehozása + beállítások seedelése. Idempotens."""
    pool = _get_pool()
    raw_conn = pool.getconn()
    raw_conn.autocommit = False
    try:
        cur = raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("CREATE EXTENSION IF NOT EXISTS citext")

        # ---------- AUTH ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email CITEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT,
                failed_login_count INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            )
        """)
        # Brute-force védelem oszlopai meglévő DB-hez (idempotens, NO FALLBACK-barát).
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TEXT")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_activity_at TEXT,
                ip TEXT,
                user_agent TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")

        # ---------- ADMIN API KULCSOK (Fernet-titkosítva) ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_api_keys (
                id SERIAL PRIMARY KEY,
                label TEXT NOT NULL,
                encrypted_value BYTEA NOT NULL,
                masked_preview TEXT NOT NULL,
                organization_id TEXT,
                organization_name TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                last_tested_at TEXT,
                last_test_ok BOOLEAN,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )
        """)

        # ---------- GLOBÁLIS BEÁLLÍTÁSOK ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # ---------- TEVÉKENYSÉGNAPLÓ ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                detail TEXT,
                ip TEXT,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC)")

        # ---------- GYŰJTÉS-FUTÁSOK (audit + observability) ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_runs (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                trigger TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                rows_upserted INTEGER NOT NULL DEFAULT 0,
                params JSONB,
                error TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_runs_src ON sync_runs(source, started_at DESC)")

        # ---------- USAGE TÉNYEK ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_facts (
                id BIGSERIAL PRIMARY KEY,
                bucket_start TIMESTAMPTZ NOT NULL,
                bucket_end TIMESTAMPTZ NOT NULL,
                bucket_width TEXT NOT NULL,
                api_key_id TEXT,
                workspace_id TEXT,
                model TEXT,
                service_tier TEXT,
                context_window TEXT,
                inference_geo TEXT,
                account_id TEXT,
                service_account_id TEXT,
                uncached_input_tokens BIGINT NOT NULL DEFAULT 0,
                cache_creation_1h_tokens BIGINT NOT NULL DEFAULT 0,
                cache_creation_5m_tokens BIGINT NOT NULL DEFAULT 0,
                cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                web_search_requests BIGINT NOT NULL DEFAULT 0,
                dim_hash TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                UNIQUE (bucket_start, bucket_width, dim_hash)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_bucket ON usage_facts(bucket_start)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_facts(model)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_ws ON usage_facts(workspace_id)")

        # ---------- USAGE ÓRÁS (csak a szervezet-egyenleg pontos-idő számításához) ----------
        # A mai napot a collector órásban kéri le; itt (modellenként) órás bontásban is
        # eltároljuk, hogy a horgony napján csak a horgony utáni órák költsége számítson.
        # KÜLÖN tábla — a többi (1d) lekérdezést nem érinti, nincs dupla számolás. A
        # collector néhány naposnál régebbi sorokat törli (csak a friss horgonyhoz kell).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_hourly_facts (
                bucket_start TIMESTAMPTZ NOT NULL,
                model TEXT,
                uncached_input_tokens BIGINT NOT NULL DEFAULT 0,
                cache_creation_1h_tokens BIGINT NOT NULL DEFAULT 0,
                cache_creation_5m_tokens BIGINT NOT NULL DEFAULT 0,
                cache_read_input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                web_search_requests BIGINT NOT NULL DEFAULT 0,
                dim_hash TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                UNIQUE (bucket_start, dim_hash)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_hourly_bs ON usage_hourly_facts(bucket_start)")

        # ---------- COST TÉNYEK ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cost_facts (
                id BIGSERIAL PRIMARY KEY,
                bucket_start TIMESTAMPTZ NOT NULL,
                bucket_end TIMESTAMPTZ NOT NULL,
                bucket_width TEXT NOT NULL DEFAULT '1d',
                workspace_id TEXT,
                description TEXT,
                cost_type TEXT,
                model TEXT,
                token_type TEXT,
                context_window TEXT,
                service_tier TEXT,
                inference_geo TEXT,
                currency TEXT NOT NULL DEFAULT 'USD',
                amount_cents NUMERIC(24,8) NOT NULL DEFAULT 0,
                dim_hash TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                UNIQUE (bucket_start, bucket_width, dim_hash)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_bucket ON cost_facts(bucket_start)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_ws ON cost_facts(workspace_id)")

        # ---------- MODELL-ÁRAZÁS (becsült költséghez, szerkeszthető) ----------
        # A becsült költség = usage_facts token-darabszámok × ezen árak. A model_pattern
        # előtag-illesztés a usage_facts.model-re (a SQL a leghosszabb prefixet választja).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_pattern TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                input_usd_per_mtok NUMERIC(12,4) NOT NULL,
                cache_write_5m_usd_per_mtok NUMERIC(12,4) NOT NULL,
                cache_write_1h_usd_per_mtok NUMERIC(12,4) NOT NULL,
                cache_read_usd_per_mtok NUMERIC(12,4) NOT NULL,
                output_usd_per_mtok NUMERIC(12,4) NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 100,
                source TEXT NOT NULL DEFAULT 'seed',
                updated_at TEXT NOT NULL
            )
        """)

        # ---------- CLAUDE CODE TÉNYEK (napi, aktoronként) ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS claude_code_facts (
                id BIGSERIAL PRIMARY KEY,
                day DATE NOT NULL,
                actor_type TEXT,
                actor_email TEXT,
                actor_api_key_name TEXT,
                customer_type TEXT,
                terminal_type TEXT,
                num_sessions BIGINT NOT NULL DEFAULT 0,
                lines_added BIGINT NOT NULL DEFAULT 0,
                lines_removed BIGINT NOT NULL DEFAULT 0,
                commits BIGINT NOT NULL DEFAULT 0,
                pull_requests BIGINT NOT NULL DEFAULT 0,
                edit_accepted BIGINT NOT NULL DEFAULT 0,
                edit_rejected BIGINT NOT NULL DEFAULT 0,
                multi_edit_accepted BIGINT NOT NULL DEFAULT 0,
                multi_edit_rejected BIGINT NOT NULL DEFAULT 0,
                write_accepted BIGINT NOT NULL DEFAULT 0,
                write_rejected BIGINT NOT NULL DEFAULT 0,
                notebook_edit_accepted BIGINT NOT NULL DEFAULT 0,
                notebook_edit_rejected BIGINT NOT NULL DEFAULT 0,
                total_input_tokens BIGINT NOT NULL DEFAULT 0,
                total_output_tokens BIGINT NOT NULL DEFAULT 0,
                estimated_cost_cents NUMERIC(24,8) NOT NULL DEFAULT 0,
                dim_hash TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                UNIQUE (day, dim_hash)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cc_day ON claude_code_facts(day)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS claude_code_model_facts (
                id BIGSERIAL PRIMARY KEY,
                fact_id BIGINT NOT NULL REFERENCES claude_code_facts(id) ON DELETE CASCADE,
                model TEXT,
                input_tokens BIGINT NOT NULL DEFAULT 0,
                output_tokens BIGINT NOT NULL DEFAULT 0,
                cache_read_tokens BIGINT NOT NULL DEFAULT 0,
                cache_creation_tokens BIGINT NOT NULL DEFAULT 0,
                estimated_cost_cents NUMERIC(24,8) NOT NULL DEFAULT 0
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ccm_fact ON claude_code_model_facts(fact_id)")

        # ---------- METAADAT-SNAPSHOTOK (ID → név leképezés) ----------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT,
                display_color TEXT,
                archived_at TEXT,
                created_at TEXT,
                raw JSONB,
                synced_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS org_api_keys (
                id TEXT PRIMARY KEY,
                name TEXT,
                workspace_id TEXT,
                status TEXT,
                partial_key_hint TEXT,
                created_at TEXT,
                raw JSONB,
                synced_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS org_members (
                id TEXT PRIMARY KEY,
                email TEXT,
                name TEXT,
                role TEXT,
                raw JSONB,
                synced_at TEXT NOT NULL
            )
        """)

        # ---------- FELHASZNÁLÓI HATÓKÖR (viewer → API kulcs / workspace) ----------
        # Az app-bejelentkezés (users) és az Anthropic erőforrások összerendelése. A
        # viewer csak a hozzárendelt kulcs(ok)/workspace(ek) adatát látja; admin korlátlan.
        # Szándékosan NINCS FK az org_api_keys/workspaces felé: azok újraszinkronnál
        # cserélődhetnek — ha egy id eltűnik, a scope-sor egyszerűen nem illeszkedik semmire.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_scope_api_keys (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                api_key_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, api_key_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_scope_workspaces (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, workspace_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_versions (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)

        # ---------- BEÁLLÍTÁSOK SEED (csak ha hiányzik) ----------
        import json
        ts = now_iso()
        for key, value in DEFAULT_SETTINGS.items():
            cur.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO NOTHING""",
                (key, json.dumps(value), ts),
            )

        # ---------- MODELL-ÁRAZÁS SEED (csak az ELSŐ induláskor, üres táblára) ----------
        # Fontos: csak akkor seedelünk, ha a tábla teljesen üres. Így ha az admin
        # szándékosan TÖRÖL egy seed-modellt (a rács-szerkesztőből), az újraindításkor
        # NEM támad fel. (Az ON CONFLICT DO NOTHING csak a meglévő sorokat védi, a
        # törölteket nem — ezért kell az üres-tábla feltétel.)
        cur.execute("SELECT 1 FROM model_pricing LIMIT 1")
        if cur.fetchone() is None:
            for pat, disp, p_in, p5m, p1h, p_rd, p_out, so in DEFAULT_PRICING:
                cur.execute(
                    """INSERT INTO model_pricing
                       (model_pattern, display_name, input_usd_per_mtok, cache_write_5m_usd_per_mtok,
                        cache_write_1h_usd_per_mtok, cache_read_usd_per_mtok, output_usd_per_mtok,
                        sort_order, source, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'seed', %s)
                       ON CONFLICT (model_pattern) DO NOTHING""",
                    (pat, disp, p_in, p5m, p1h, p_rd, p_out, so, ts),
                )

        cur.execute(
            """INSERT INTO schema_versions (version, applied_at)
               VALUES (%s, %s) ON CONFLICT (version) DO NOTHING""",
            ("0.1.0", ts),
        )

        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        pool.putconn(raw_conn)
