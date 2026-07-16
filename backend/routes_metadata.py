"""Metaadat-snapshotok (workspaces, API kulcsok, tagok, modellek) — szűrőkhöz/címkékhez."""
from fastapi import APIRouter, Request

import auth
import scope
from database import get_db

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/workspaces")
def workspaces(request: Request):
    # A szűrő-legördülő a nézőnek csak a hatókörébe eső workspace-eket mutatja
    # (közvetlenül hozzárendelt VAGY a hozzárendelt kulcsok workspace-e).
    scp = scope.current_scope(request)
    cl, cp = scope.workspaces_scope_clause(scp)
    where = f" WHERE {cl}" if cl else ""
    with get_db() as con:
        rows = con.execute(
            f"SELECT id, name, display_color, archived_at FROM workspaces{where} ORDER BY name NULLS LAST",
            cp,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api-keys")
def api_keys(request: Request):
    # Nézőnek csak a hozzárendelt (vagy a hozzárendelt workspace-hez tartozó) kulcsok.
    scp = scope.current_scope(request)
    cl, cp = scope.api_keys_scope_clause(scp)
    where = f" WHERE {cl}" if cl else ""
    with get_db() as con:
        rows = con.execute(
            f"SELECT id, name, workspace_id, status FROM org_api_keys{where} ORDER BY name NULLS LAST",
            cp,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/members")
def members(request: Request):
    auth.get_current_user(request)
    with get_db() as con:
        rows = con.execute(
            "SELECT id, email, name, role FROM org_members ORDER BY email NULLS LAST"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/models")
def models(request: Request):
    scp = scope.current_scope(request)
    scl, sp = scope.usage_scope_clause(scp)
    where = "model IS NOT NULL" + (f" AND {scl}" if scl else "")
    with get_db() as con:
        rows = con.execute(
            f"SELECT DISTINCT model FROM usage_facts WHERE {where} ORDER BY model", sp
        ).fetchall()
    return [r["model"] for r in rows]
