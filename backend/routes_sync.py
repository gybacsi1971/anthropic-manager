"""Gyűjtés vezérlése: manuális sync, backfill, státusz, előzmény."""
import asyncio

from fastapi import APIRouter, Request, HTTPException

import auth
import admin_key_service
import collector
from database import get_db
from query_helpers import parse_range
from activity_logger import log_activity
from schemas import SyncRunRequest, BackfillRequest

router = APIRouter(prefix="/api/sync", tags=["sync"])

VALID_SOURCES = ("usage", "cost", "claude_code", "metadata")
RANGED_SOURCES = ("usage", "cost", "claude_code")


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
