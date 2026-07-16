"""Gyűjtés vezérlése: manuális sync, backfill, státusz, előzmény."""
import asyncio

from fastapi import APIRouter, Request, HTTPException

import auth
import admin_key_service
import collector
import scope
from database import get_db, now_iso
from query_helpers import parse_range
from activity_logger import log_activity
from schemas import SyncRunRequest, BackfillRequest

router = APIRouter(prefix="/api/sync", tags=["sync"])

VALID_SOURCES = ("usage", "cost", "claude_code", "metadata")
RANGED_SOURCES = ("usage", "cost", "claude_code")

# forrás → (ténytábla, rendezés-oszlop)
FACT_TABLES = {
    "usage": ("usage_facts", "bucket_start"),
    "cost": ("cost_facts", "bucket_start"),
    "claude_code": ("claude_code_facts", "day"),
}
METADATA_TABLES = ("workspaces", "org_api_keys", "org_members")
METADATA_ORDER = {"workspaces": "name NULLS LAST", "org_api_keys": "name NULLS LAST",
                   "org_members": "email NULLS LAST"}


def _run_bg(source, trigger, start=None, end=None):
    try:
        collector.run_sync(source, trigger, start, end)
    except Exception:
        # A run_sync a sync_runs sort már 'error'-ra zárta; itt elnyeljük,
        # hogy a háttértaszk ne dobjon kezeletlen kivételt.
        pass


@router.post("/run")
async def run_now(req: SyncRunRequest, request: Request):
    a = auth.get_current_admin(request)
    if req.source not in VALID_SOURCES:
        raise HTTPException(400, "Érvénytelen forrás")
    if not admin_key_service.has_active_key():
        raise HTTPException(400, "Nincs aktív Admin API kulcs")
    log_activity(a["id"], "sync_run", detail={"source": req.source})
    asyncio.create_task(asyncio.to_thread(_run_bg, req.source, "manual"))
    return {"started": True, "source": req.source}


@router.post("/backfill")
async def backfill(req: BackfillRequest, request: Request):
    a = auth.get_current_admin(request)
    if req.source not in RANGED_SOURCES:
        raise HTTPException(400, "Backfill csak usage/cost/claude_code forrásra lehetséges")
    if not admin_key_service.has_active_key():
        raise HTTPException(400, "Nincs aktív Admin API kulcs")
    start_iso, end_iso = parse_range(req.start, req.end)
    log_activity(a["id"], "sync_backfill",
                 detail={"source": req.source, "start": req.start, "end": req.end})
    asyncio.create_task(asyncio.to_thread(_run_bg, req.source, "backfill", start_iso, end_iso))
    return {"started": True, "source": req.source, "start": start_iso, "end": end_iso}


@router.get("/runs")
def runs(request: Request, limit: int = 50):
    auth.get_current_user(request)
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT %s", (min(limit, 200),)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/status")
def status(request: Request):
    auth.get_current_user(request)
    with get_db() as con:
        last = con.execute(
            """SELECT DISTINCT ON (source) source, status, trigger, started_at,
                      finished_at, rows_upserted, error
               FROM sync_runs ORDER BY source, started_at DESC"""
        ).fetchall()
        coverage = {}
        for tbl in ("usage_facts", "cost_facts"):
            r = con.execute(
                f"SELECT MIN(bucket_start) AS min_ts, MAX(bucket_start) AS max_ts, COUNT(*) AS rows FROM {tbl}"
            ).fetchone()
            coverage[tbl] = dict(r)
        cc = con.execute(
            "SELECT MIN(day) AS min_day, MAX(day) AS max_day, COUNT(*) AS rows FROM claude_code_facts"
        ).fetchone()
        coverage["claude_code_facts"] = dict(cc)
        metadata = {}
        for tbl in ("workspaces", "org_api_keys", "org_members"):
            r = con.execute(f"SELECT COUNT(*) AS rows, MAX(synced_at) AS synced_at FROM {tbl}").fetchone()
            metadata[tbl] = dict(r)
    return {
        "last_runs": [dict(r) for r in last],
        "coverage": coverage,
        "metadata": metadata,
        "active_key": admin_key_service.has_active_key(),
    }


def _metadata_scope_clause(table: str, scp):
    if table == "workspaces":
        return scope.workspaces_scope_clause(scp)
    if table == "org_api_keys":
        return scope.api_keys_scope_clause(scp)
    return "", []  # org_members: nincs hatókör-szűrés (l. routes_metadata.members)


@router.get("/runs/{run_id}/rows")
def run_rows(run_id: int, request: Request, table: str = None, limit: int = 50, offset: int = 0):
    """Egy adott gyűjtés-futás által érintett ténysorok, lapozva, néző hatókörével szűrve."""
    auth.get_current_user(request)
    scp = scope.current_scope(request)
    limit = min(max(limit, 1), 200)

    with get_db() as con:
        run = con.execute("SELECT * FROM sync_runs WHERE id = %s", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Nincs ilyen gyűjtés-futás")
        run = dict(run)
        end_bound = run["finished_at"] or now_iso()

        if run["source"] == "metadata":
            tbl = table or "org_api_keys"
            if tbl not in METADATA_TABLES:
                raise HTTPException(400, "Érvénytelen tábla")
            table_counts = {}
            for t in METADATA_TABLES:
                cl, cp = _metadata_scope_clause(t, scp)
                w = "synced_at BETWEEN %s AND %s" + (f" AND {cl}" if cl else "")
                table_counts[t] = con.execute(
                    f"SELECT COUNT(*) AS n FROM {t} WHERE {w}", [run["started_at"], end_bound, *cp]
                ).fetchone()["n"]
            cl, cp = _metadata_scope_clause(tbl, scp)
            where = "synced_at BETWEEN %s AND %s" + (f" AND {cl}" if cl else "")
            params = [run["started_at"], end_bound, *cp]
            rows = [dict(r) for r in con.execute(
                f"SELECT * FROM {tbl} WHERE {where} ORDER BY {METADATA_ORDER[tbl]} LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()]
            return {"run": run, "source": "metadata", "table": tbl, "table_counts": table_counts,
                    "rows": rows, "total": table_counts[tbl], "limit": limit, "offset": offset}

        if run["source"] == "cost" and scp is not None:
            # A cost_facts-nak nincs api_key_id oszlopa, ezért a néző (bármilyen
            # hatókörrel) itt sem lát tényleges költséget — l. routes_cost.py.
            return {"run": run, "source": "cost", "table": None, "table_counts": None,
                    "rows": [], "total": 0, "limit": limit, "offset": offset, "viewer_blocked": True}

        fact_table, order_col = FACT_TABLES[run["source"]]
        if run["source"] == "usage":
            scl, sp = scope.usage_scope_clause(scp)
        elif run["source"] == "claude_code":
            scl, sp = scope.claude_code_scope_clause(scp, con)
        else:
            scl, sp = "", []
        where = "collected_at BETWEEN %s AND %s" + (f" AND {scl}" if scl else "")
        params = [run["started_at"], end_bound, *sp]
        total = con.execute(f"SELECT COUNT(*) AS n FROM {fact_table} WHERE {where}", params).fetchone()["n"]
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM {fact_table} WHERE {where} ORDER BY {order_col} LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()]
        return {"run": run, "source": run["source"], "table": None, "table_counts": None,
                "rows": rows, "total": total, "limit": limit, "offset": offset}
