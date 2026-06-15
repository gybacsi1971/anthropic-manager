"""
Globális beállítások kezelése (settings tábla, JSONB értékek).

A beállításokat az init seedeli explicit alapértékekkel (database.DEFAULT_SETTINGS),
így a kód a DB-ből olvas — hiányzó kulcs KeyError (NO FALLBACK, nincs beégetett default).
"""
import json

from database import get_db, now_iso


def get_setting(key: str):
    """Egy beállítás értéke. Hiány → KeyError."""
    with get_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    if row is None:
        raise KeyError(f"Hiányzó beállítás: {key}")
    return row["value"]  # psycopg2 a JSONB-t már Python-objektumként adja


def get_all_settings() -> dict:
    with get_db() as con:
        rows = con.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value) -> None:
    with get_db() as con:
        con.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
            (key, json.dumps(value), now_iso()),
        )


def set_many(values: dict) -> None:
    ts = now_iso()
    with get_db() as con:
        for key, value in values.items():
            con.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE
                       SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (key, json.dumps(value), ts),
            )
