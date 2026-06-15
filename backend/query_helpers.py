"""Közös segédfüggvények az aggregáló végpontokhoz (dátumtartomány, group-by védelem)."""
from datetime import timedelta

from dateutil import parser as dtparser
from fastapi import HTTPException

from database import get_db


# Üres dimenzió-érték helyett megjelenített, beszédes címke (a group_by szerint).
GROUP_SENTINELS = {
    "workspace_id": "(alapértelmezett workspace)",
    "api_key_id": "(Konzol / nincs kulcs)",
    "model": "(ismeretlen modell)",
    "service_tier": "(nincs)",
    "context_window": "(nincs)",
    "cost_type": "(nincs)",
    "token_type": "(nincs)",
}


def parse_range(start: str, end: str):
    """start/end (YYYY-MM-DD vagy RFC3339) → (start_iso_inkluzív, end_iso_exkluzív).

    Az end napot inkluzívvá tesszük: a felső határ a következő nap 00:00Z.
    """
    try:
        s = dtparser.isoparse(start).date()
        e = dtparser.isoparse(end).date()
    except Exception:
        raise HTTPException(400, "Érvénytelen dátum (várt formátum: YYYY-MM-DD)")
    if e < s:
        raise HTTPException(400, "A záró dátum nem lehet korábbi a kezdő dátumnál")
    return s.isoformat() + "T00:00:00Z", (e + timedelta(days=1)).isoformat() + "T00:00:00Z"


def parse_date_range(start: str, end: str):
    """DATE oszlophoz: (start_date_str, end_exkluzív_date_str), YYYY-MM-DD."""
    try:
        s = dtparser.isoparse(start).date()
        e = dtparser.isoparse(end).date()
    except Exception:
        raise HTTPException(400, "Érvénytelen dátum (várt formátum: YYYY-MM-DD)")
    if e < s:
        raise HTTPException(400, "A záró dátum nem lehet korábbi a kezdő dátumnál")
    return s.isoformat(), (e + timedelta(days=1)).isoformat()


def safe_group_column(group_by: str, allowed: dict) -> str:
    """A group_by paramétert egy whitelistre képezi (SQL-injection ellen).

    allowed: {api_neve: oszlopnev}. 'none'/'' → None (nincs csoportosítás).
    """
    if not group_by or group_by == "none":
        return None
    if group_by not in allowed:
        raise HTTPException(400, f"Érvénytelen group_by: {group_by}")
    return allowed[group_by]


def assemble_timeseries(rows):
    """rows: [{day, grp, val}] (grp sosem None) → (days, [(grp, [val/day])]).

    A sorozatok összérték szerint csökkenően rendezve.
    """
    days = sorted({r["day"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    groups: dict = {}
    for r in rows:
        g = r["grp"]
        groups.setdefault(g, [0.0] * len(days))
        groups[g][idx[r["day"]]] = float(r["val"]) if r["val"] is not None else 0.0
    ordered = sorted(groups.items(), key=lambda kv: sum(kv[1]), reverse=True)
    return days, ordered


def resolve_labels(group_by: str, keys) -> dict:
    """group key értékek → megjelenítendő címke. Workspace/API-kulcs ID → név."""
    out = {k: k for k in keys}
    if group_by == "workspace_id":
        with get_db() as con:
            rows = con.execute("SELECT id, name FROM workspaces").fetchall()
        m = {r["id"]: r["name"] for r in rows}
        for k in keys:
            if k in m and m[k]:
                out[k] = m[k]
    elif group_by == "api_key_id":
        with get_db() as con:
            rows = con.execute("SELECT id, name FROM org_api_keys").fetchall()
        m = {r["id"]: r["name"] for r in rows}
        for k in keys:
            if k in m and m[k]:
                out[k] = m[k]
    return out
