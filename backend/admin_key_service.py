"""
Anthropic Admin API kulcsok (sk-ant-admin...) kezelése.

A kulcsok Fernet-titkosítással az `admin_api_keys` táblában élnek. A teljes érték
SOSEM hagyja el a backendet — a listázó végpontok csak a maszkolt verziót adják.

A titkosító kulcs (API_KEY_ENCRYPTION_KEY env) KÖTELEZŐ — hiánya/hibája startup
error. NO FALLBACK.
"""
import os

import psycopg2

from cryptography.fernet import Fernet

from config import get_encryption_key
from database import get_db, now_iso


_FERNET_CACHE = None


def _get_fernet() -> Fernet:
    global _FERNET_CACHE
    if _FERNET_CACHE is None:
        key = get_encryption_key()  # RuntimeError, ha hiányzik
        _FERNET_CACHE = Fernet(key.encode() if isinstance(key, str) else key)
    return _FERNET_CACHE


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "..." + value[-4:]
    return value[:12] + "..." + value[-4:]


def encrypt(plain: str) -> bytes:
    return _get_fernet().encrypt(plain.encode())


def decrypt(blob) -> str:
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    return _get_fernet().decrypt(blob).decode()


def list_keys(include_deleted: bool = False) -> list[dict]:
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with get_db() as con:
        rows = con.execute(
            f"""SELECT id, label, masked_preview, organization_id, organization_name,
                       is_active, last_tested_at, last_test_ok, created_at, updated_at, deleted_at
                FROM admin_api_keys {where} ORDER BY id ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def create_key(label: str, value: str) -> int:
    label = (label or "").strip()
    value = (value or "").strip()
    if not label:
        raise ValueError("A címke nem lehet üres")
    if not value:
        raise ValueError("A kulcs értéke nem lehet üres")
    ts = now_iso()
    with get_db() as con:
        cur = con.execute(
            """INSERT INTO admin_api_keys
               (label, encrypted_value, masked_preview, is_active, created_at, updated_at)
               VALUES (%s, %s, %s, TRUE, %s, %s) RETURNING id""",
            (label, psycopg2.Binary(encrypt(value)), _mask(value), ts, ts),
        )
        return cur.fetchone()["id"]


def get_key_value(key_id: int) -> str:
    with get_db() as con:
        row = con.execute(
            "SELECT encrypted_value FROM admin_api_keys WHERE id = %s AND deleted_at IS NULL",
            (key_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"admin_api_keys.id={key_id} nem található vagy törölt")
    return decrypt(row["encrypted_value"])


def get_active_key() -> dict:
    """A gyűjtéshez használt aktív kulcs (legkisebb id az aktívak közül).

    Visszaad: {id, label, value, organization_id}. Ha nincs aktív kulcs → RuntimeError.
    """
    with get_db() as con:
        row = con.execute(
            """SELECT id, label, encrypted_value, organization_id
               FROM admin_api_keys
               WHERE is_active = TRUE AND deleted_at IS NULL
               ORDER BY id ASC LIMIT 1"""
        ).fetchone()
    if not row:
        raise RuntimeError("Nincs aktív Admin API kulcs beállítva — vegyél fel egyet az Admin kulcsok oldalon.")
    return {
        "id": row["id"],
        "label": row["label"],
        "value": decrypt(row["encrypted_value"]),
        "organization_id": row["organization_id"],
    }


def has_active_key() -> bool:
    with get_db() as con:
        row = con.execute(
            "SELECT 1 FROM admin_api_keys WHERE is_active = TRUE AND deleted_at IS NULL LIMIT 1"
        ).fetchone()
    return row is not None


def update_label(key_id: int, label: str) -> None:
    label = (label or "").strip()
    if not label:
        raise ValueError("A címke nem lehet üres")
    with get_db() as con:
        con.execute("UPDATE admin_api_keys SET label = %s, updated_at = %s WHERE id = %s",
                    (label, now_iso(), key_id))


def set_active(key_id: int, active: bool) -> None:
    with get_db() as con:
        con.execute("UPDATE admin_api_keys SET is_active = %s, updated_at = %s WHERE id = %s",
                    (bool(active), now_iso(), key_id))


def soft_delete(key_id: int) -> None:
    ts = now_iso()
    with get_db() as con:
        con.execute(
            """UPDATE admin_api_keys
               SET deleted_at = %s, is_active = FALSE, updated_at = %s
               WHERE id = %s AND deleted_at IS NULL""",
            (ts, ts, key_id),
        )


def import_from_env_once() -> int:
    """Startup-hívás: az `ANTHROPIC_ADMIN_KEY[_N]` env változókat egyszer
    beimportálja a DB-be (Fernet-titkosítva), majd a felületen kezelhetők.

    Idempotens a **label** (a változónév) alapján — a következő indításkor
    ugyanazzal a labellel nem importál újra. A kényelmi `.env` bejegyzés így
    nem marad „nyersen" használatban: a tényleges kulcs titkosítva a DB-ben él.
    Visszatérés: a beszúrt új rekordok száma.
    """
    discovered: list[tuple[str, str]] = []
    base = os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip()
    if base:
        discovered.append(("ANTHROPIC_ADMIN_KEY", base))
    for i in range(2, 11):
        val = os.environ.get(f"ANTHROPIC_ADMIN_KEY_{i}", "").strip()
        if val:
            discovered.append((f"ANTHROPIC_ADMIN_KEY_{i}", val))

    if not discovered:
        return 0

    inserted = 0
    with get_db() as con:
        used = {
            r["label"]
            for r in con.execute(
                "SELECT label FROM admin_api_keys WHERE deleted_at IS NULL"
            ).fetchall()
        }
        ts = now_iso()
        for label, value in discovered:
            if label in used:
                continue
            con.execute(
                """INSERT INTO admin_api_keys
                   (label, encrypted_value, masked_preview, is_active, created_at, updated_at)
                   VALUES (%s, %s, %s, TRUE, %s, %s)""",
                (label, psycopg2.Binary(encrypt(value)), _mask(value), ts, ts),
            )
            used.add(label)
            inserted += 1
    return inserted


def record_test_result(key_id: int, ok: bool, organization_id: str = None,
                       organization_name: str = None) -> None:
    with get_db() as con:
        con.execute(
            """UPDATE admin_api_keys
               SET last_tested_at = %s, last_test_ok = %s,
                   organization_id = COALESCE(%s, organization_id),
                   organization_name = COALESCE(%s, organization_name),
                   updated_at = %s
               WHERE id = %s""",
            (now_iso(), bool(ok), organization_id, organization_name, now_iso(), key_id),
        )
