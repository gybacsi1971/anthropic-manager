"""
Felhasználói hatókör (adat-szűkítés) — egy forrás az igazságra.

Az admin korlátlan; a viewer csak a hozzárendelt API kulcs(ok)/workspace(ek) adatát
látja (lásd `user_scope_api_keys` / `user_scope_workspaces`). A `current_scope()` maga
végzi az auth-ellenőrzést (401, ha nincs bejelentkezés), így a végpontokban az eddigi
`auth.get_current_user(request)` hívást váltja ki.

A klauzula-helperek a meglévő `fcl` (clause-lista) / `fp` (param-lista) mintába
illeszkednek: a visszaadott SQL-töredék a WHERE szűrői UTÁN fűzhető, a paraméterei a
param-lista VÉGÉRE — így a meglévő param-sorrend (pl. `[sentinel, s, e, *fp]`) nem törik.

NO FALLBACK: a hatókör nélküli viewer-nek a klauzula `FALSE` (üres eredmény), SOHA nem
"minden adat".
"""
import auth
from database import get_db


def current_scope(request):
    """Visszaad: None (admin → korlátlan) | {api_key_ids, workspace_ids, email} (viewer).

    Maga hívja az `auth.get_current_user`-t → 401, ha nincs bejelentkezés.
    """
    user = auth.get_current_user(request)
    if user.get("role") == "admin":
        return None
    with get_db() as con:
        keys = [r["api_key_id"] for r in con.execute(
            "SELECT api_key_id FROM user_scope_api_keys WHERE user_id = %s", (user["id"],)
        ).fetchall()]
        wss = [r["workspace_id"] for r in con.execute(
            "SELECT workspace_id FROM user_scope_workspaces WHERE user_id = %s", (user["id"],)
        ).fetchall()]
    return {"api_key_ids": keys, "workspace_ids": wss, "email": user["email"]}


def usage_scope_clause(scope, prefix: str = ""):
    """usage_facts (api_key_id + workspace_id) — kulcs VAGY workspace illesztés.

    Visszaad: (clause, params). admin → ("", []); hatókör nélküli viewer → ("FALSE", []).
    `prefix`: pl. "uf." aliasolt lekérdezéshez.
    """
    if scope is None:
        return "", []
    keys, wss = scope["api_key_ids"], scope["workspace_ids"]
    if not keys and not wss:
        return "FALSE", []
    parts, params = [], []
    if keys:
        parts.append(f"{prefix}api_key_id = ANY(%s)")
        params.append(keys)
    if wss:
        parts.append(f"{prefix}workspace_id = ANY(%s)")
        params.append(wss)
    return "(" + " OR ".join(parts) + ")", params


def claude_code_scope_clause(scope, con):
    """claude_code_facts — nincs api_key_id/workspace_id, csak actor_email / actor_api_key_name.

    A viewer e-mailje VAGY a hozzárendelt kulcsok (és a hozzárendelt workspace-ek
    kulcsainak) neve illeszkedjen. A kulcsnév-feloldáshoz nyitott DB-kapcsolat kell.
    """
    if scope is None:
        return "", []
    key_ids = set(scope["api_key_ids"])
    if scope["workspace_ids"]:
        key_ids |= {r["id"] for r in con.execute(
            "SELECT id FROM org_api_keys WHERE workspace_id = ANY(%s)", (list(scope["workspace_ids"]),)
        ).fetchall()}
    names = []
    if key_ids:
        names = [r["name"] for r in con.execute(
            "SELECT name FROM org_api_keys WHERE id = ANY(%s) AND name IS NOT NULL", (list(key_ids),)
        ).fetchall()]
    parts, params = ["actor_email = %s"], [scope["email"]]
    if names:
        parts.append("actor_api_key_name = ANY(%s)")
        params.append(names)
    return "(" + " OR ".join(parts) + ")", params
