"""
Autentikáció és jogosultság-kezelés (a referencia auth.py mintájára).
- Jelszó: PBKDF2-HMAC-SHA256 (stdlib)
- Session token: secrets.token_urlsafe, sessions táblában, lejárati idővel
- HttpOnly cookie → a JS nem fér hozzá (XSS-védelem)
Szerepkörök: 'admin' (mindenhez) | 'viewer' (csak nézet).
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import Request, HTTPException
import psycopg2.errors

from database import get_db, row_to_dict, now_iso


SESSION_EXPIRY_DAYS = 7        # internetre kitett admin-eszközhöz a 30 nap sok
SESSION_IDLE_HOURS = 24        # ennyi inaktivitás után a session érvénytelen
COOKIE_NAME = "anthropic_manager_session"

PBKDF2_ITERATIONS = 200_000
PBKDF2_ALGORITHM = "sha256"
SALT_BYTES = 16
HASH_BYTES = 32

# Brute-force védelem: N hibás próba után a fiók LOCKOUT_MINUTES percre zárolva.
MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 10
LOCKOUT_MINUTES = 15

VALID_ROLES = ("admin", "viewer")


# ============================================================
# JELSZÓ
# ============================================================

def hash_password(password: str) -> str:
    if not password:
        raise ValueError("A jelszó nem lehet üres")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM, password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=HASH_BYTES
    )
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or not password_hash.startswith("pbkdf2$"):
        return False
    try:
        _, iters_str, salt_hex, hash_hex = password_hash.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac(
            PBKDF2_ALGORITHM, password.encode("utf-8"), salt, int(iters_str), dklen=len(expected)
        )
        return secrets.compare_digest(expected, actual)
    except Exception:
        return False


# ============================================================
# SESSION
# ============================================================

def create_session(user_id: int, ip: Optional[str] = None,
                   user_agent: Optional[str] = None) -> Tuple[str, str]:
    token = secrets.token_urlsafe(48)
    now = now_iso()
    expires_at = (datetime.utcnow() + timedelta(days=SESSION_EXPIRY_DAYS)) \
        .isoformat(timespec="seconds") + "Z"
    with get_db() as con:
        con.execute(
            """INSERT INTO sessions (token, user_id, created_at, expires_at, last_activity_at, ip, user_agent)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (token, user_id, now, expires_at, now, ip, user_agent),
        )
        con.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (now, user_id))
    return token, expires_at


def get_user_by_token(token: str) -> Optional[dict]:
    if not token:
        return None
    idle_cutoff = (datetime.utcnow() - timedelta(hours=SESSION_IDLE_HOURS)) \
        .isoformat(timespec="seconds") + "Z"
    with get_db() as con:
        row = con.execute(
            """SELECT u.* FROM users u
               INNER JOIN sessions s ON s.user_id = u.id
               WHERE s.token = %s AND s.expires_at > %s
                 AND COALESCE(s.last_activity_at, s.created_at) > %s
                 AND u.is_active = TRUE""",
            (token, now_iso(), idle_cutoff),
        ).fetchone()
        if not row:
            return None
        # Aktivitás frissítése: minden hitelesített kérés tolja az idle-ablakot.
        con.execute("UPDATE sessions SET last_activity_at = %s WHERE token = %s",
                    (now_iso(), token))
    user = row_to_dict(row)
    user.pop("password_hash", None)
    return user


def revoke_session(token: str) -> None:
    with get_db() as con:
        con.execute("DELETE FROM sessions WHERE token = %s", (token,))


def revoke_all_sessions_for_user(user_id: int) -> None:
    with get_db() as con:
        con.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def update_session_activity(token: str) -> None:
    with get_db() as con:
        con.execute("UPDATE sessions SET last_activity_at = %s WHERE token = %s",
                    (now_iso(), token))


# ============================================================
# BRUTE-FORCE VÉDELEM (fiók-zárolás)
# ============================================================

def is_locked(user: dict) -> bool:
    """A fiók zárolva van-e (a `locked_until` a jövőben van-e)."""
    locked_until = user.get("locked_until") if user else None
    return bool(locked_until and locked_until > now_iso())


def register_failed_login(user_id: int) -> None:
    """Hibás jelszó: számláló +1; a küszöböt elérve LOCKOUT_MINUTES percre zárol
    és nullázza a számlálót (a zárolás lejárta után tiszta lappal indul)."""
    locked_until = (datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)) \
        .isoformat(timespec="seconds") + "Z"
    # A SET kifejezések mind a RÉGI sorértékre hivatkoznak, így a `+ 1` a frissítés
    # előtti számlálót használja. A küszöböt elérve: zárol és nullázza a számlálót.
    with get_db() as con:
        con.execute(
            """UPDATE users
               SET locked_until = CASE
                       WHEN failed_login_count + 1 >= %s THEN %s
                       ELSE locked_until END,
                   failed_login_count = CASE
                       WHEN failed_login_count + 1 >= %s THEN 0
                       ELSE failed_login_count + 1 END
               WHERE id = %s""",
            (MAX_FAILED_LOGINS, locked_until, MAX_FAILED_LOGINS, user_id),
        )


def clear_failed_login(user_id: int) -> None:
    """Sikeres login: számláló és zárolás nullázása."""
    with get_db() as con:
        con.execute(
            "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = %s",
            (user_id,),
        )


# ============================================================
# FELHASZNÁLÓK
# ============================================================

def get_user_by_email(email: str) -> Optional[dict]:
    if not email:
        return None
    with get_db() as con:
        row = con.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return row_to_dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with get_db() as con:
        row = con.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return None
    user = row_to_dict(row)
    user.pop("password_hash", None)
    return user


def list_users() -> list:
    with get_db() as con:
        rows = con.execute(
            """SELECT id, email, name, role, is_active, created_at, last_login_at
               FROM users ORDER BY id ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def has_any_user() -> bool:
    with get_db() as con:
        row = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def ensure_default_admin() -> bool:
    """Üres adatbázisnál (első indulás) létrehozza az alapértelmezett admin
    felhasználót az `ADMIN_EMAIL` / `ADMIN_PASSWORD` / `ADMIN_NAME` env-ből.

    Csak akkor fut, ha még NINCS egyetlen felhasználó sem — így a felületen
    később módosított jelszót nem írja felül. Üres env esetén nem csinál semmit
    (marad a /setup varázsló). Visszaad: True, ha létrehozott.
    """
    if has_any_user():
        return False
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    name = os.environ.get("ADMIN_NAME", "").strip() or "Adminisztrátor"
    if not email or not password:
        return False
    create_user(email, password, name, role="admin")
    return True


def create_user(email: str, password: str, name: str, role: str = "viewer") -> dict:
    if role not in VALID_ROLES:
        raise ValueError("Érvénytelen szerepkör")
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not email or not name or not password:
        raise ValueError("Email, név és jelszó kötelező")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A jelszónak legalább {MIN_PASSWORD_LENGTH} karakter hosszúnak kell lennie")
    pw_hash = hash_password(password)
    now = now_iso()
    with get_db() as con:
        try:
            cur = con.execute(
                """INSERT INTO users (email, password_hash, name, role, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (email, pw_hash, name, role, now, now),
            )
            user_id = cur.fetchone()["id"]
        except psycopg2.errors.UniqueViolation:
            raise ValueError("Már létezik felhasználó ezzel az email-címmel")
    return get_user_by_id(user_id)


def update_user(user_id: int, name: str = None, role: str = None, is_active: bool = None) -> None:
    sets, params = [], []
    if name is not None:
        sets.append("name = %s"); params.append(name.strip())
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError("Érvénytelen szerepkör")
        sets.append("role = %s"); params.append(role)
    if is_active is not None:
        sets.append("is_active = %s"); params.append(bool(is_active))
    if not sets:
        return
    sets.append("updated_at = %s"); params.append(now_iso())
    params.append(user_id)
    with get_db() as con:
        con.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", params)


def update_user_password(user_id: int, new_password: str) -> None:
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A jelszónak legalább {MIN_PASSWORD_LENGTH} karakter hosszúnak kell lennie")
    with get_db() as con:
        con.execute("UPDATE users SET password_hash = %s, updated_at = %s WHERE id = %s",
                    (hash_password(new_password), now_iso(), user_id))
    revoke_all_sessions_for_user(user_id)


def delete_user(user_id: int) -> None:
    with get_db() as con:
        con.execute("DELETE FROM users WHERE id = %s", (user_id,))


def get_user_scope(user_id: int) -> dict:
    """A felhasználóhoz rendelt API kulcs- és workspace-id-k (kézi hatókör)."""
    with get_db() as con:
        keys = [r["api_key_id"] for r in con.execute(
            "SELECT api_key_id FROM user_scope_api_keys WHERE user_id = %s ORDER BY api_key_id",
            (user_id,),
        ).fetchall()]
        wss = [r["workspace_id"] for r in con.execute(
            "SELECT workspace_id FROM user_scope_workspaces WHERE user_id = %s ORDER BY workspace_id",
            (user_id,),
        ).fetchall()]
    return {"api_key_ids": keys, "workspace_ids": wss}


def set_user_scope(user_id: int, api_key_ids: list, workspace_ids: list) -> None:
    """A hatókör teljes cseréje (törlés + beszúrás egy tranzakcióban, dedup-olva)."""
    now = now_iso()
    keys = sorted({k for k in (api_key_ids or []) if k})
    wss = sorted({w for w in (workspace_ids or []) if w})
    with get_db() as con:
        con.execute("DELETE FROM user_scope_api_keys WHERE user_id = %s", (user_id,))
        con.execute("DELETE FROM user_scope_workspaces WHERE user_id = %s", (user_id,))
        for k in keys:
            con.execute(
                "INSERT INTO user_scope_api_keys (user_id, api_key_id, created_at) VALUES (%s, %s, %s)",
                (user_id, k, now),
            )
        for w in wss:
            con.execute(
                "INSERT INTO user_scope_workspaces (user_id, workspace_id, created_at) VALUES (%s, %s, %s)",
                (user_id, w, now),
            )


def count_admins(exclude_user_id: int = None) -> int:
    with get_db() as con:
        if exclude_user_id is not None:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = TRUE AND id <> %s",
                (exclude_user_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = TRUE"
            ).fetchone()
    return row["c"]


# ============================================================
# FastAPI DEPENDENCIES
# ============================================================

def _get_token_from_request(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def get_client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip() or None
    return request.client.host if request.client else None


def get_current_user_optional(request: Request) -> Optional[dict]:
    token = _get_token_from_request(request)
    if not token:
        return None
    return get_user_by_token(token)


def get_current_user(request: Request) -> dict:
    user = get_current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Bejelentkezés szükséges")
    return user


def get_current_admin(request: Request) -> dict:
    user = get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Csak adminisztrátorok férhetnek hozzá")
    return user
