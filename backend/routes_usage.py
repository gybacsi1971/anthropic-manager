"""Usage (token-felhasználás) analitika a helyi usage_facts táblából."""
from fastapi import APIRouter, Request, Query, HTTPException

import scope
from database import get_db
from query_helpers import (
    parse_range, safe_group_column, assemble_timeseries, resolve_labels, GROUP_SENTINELS,
)

router = APIRouter(prefix="/api/usage", tags=["usage"])

METRICS = {
    "total_tokens": "(uncached_input_tokens + cache_creation_1h_tokens + cache_creation_5m_tokens + cache_read_input_tokens + output_tokens)",
    "input": "uncached_input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_creation": "(cache_creation_1h_tokens + cache_creation_5m_tokens)",
    "web_search": "web_search_requests",
}
GROUPS = {
    "model": "model",
    "workspace_id": "workspace_id",
    "api_key_id": "api_key_id",
    "service_tier": "service_tier",
    "context_window": "context_window",
}
DAY_EXPR = "to_char(date_trunc('day', bucket_start AT TIME ZONE 'UTC'), 'YYYY-MM-DD')"


def _filters(workspace_id, api_key_id, model, service_tier, context_window):
    clauses, params = [], []
    for col, vals in (
        ("workspace_id", workspace_id), ("api_key_id", api_key_id), ("model", model),
        ("service_tier", service_tier), ("context_window", context_window),
    ):
        if vals:
            clauses.append(f"{col} = ANY(%s)")
            params.append(list(vals))
    return clauses, params


def _metric_expr(metric: str) -> str:
    if metric not in METRICS:
        raise HTTPException(400, f"Érvénytelen metrika: {metric}")
    return METRICS[metric]


@router.get("/summary")
def summary(request: Request, start: str, end: str,
            workspace_id: list[str] = Query(None), api_key_id: list[str] = Query(None),
            model: list[str] = Query(None), service_tier: list[str] = Query(None),
            context_window: list[str] = Query(None)):
    scp = scope.current_scope(request)
    s, e = parse_range(start, end)
    fcl, fp = _filters(workspace_id, api_key_id, model, service_tier, context_window)
    scl, sp = scope.usage_scope_clause(scp)
    if scl:
        fcl.append(scl); fp.extend(sp)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        row = con.execute(
            f"""SELECT COALESCE(SUM(uncached_input_tokens),0) AS input,
                       COALESCE(SUM(output_tokens),0) AS output,
                       COALESCE(SUM(cache_read_input_tokens),0) AS cache_read,
                       COALESCE(SUM(cache_creation_1h_tokens + cache_creation_5m_tokens),0) AS cache_creation,
                       COALESCE(SUM(web_search_requests),0) AS web_search
                FROM usage_facts WHERE {where}""",
            [s, e, *fp],
        ).fetchone()
    d = {k: int(v) for k, v in dict(row).items()}
    d["total_tokens"] = d["input"] + d["output"] + d["cache_read"] + d["cache_creation"]
    return d


@router.get("/timeseries")
def timeseries(request: Request, start: str, end: str, group_by: str = "none",
               metric: str = "total_tokens",
               workspace_id: list[str] = Query(None), api_key_id: list[str] = Query(None),
               model: list[str] = Query(None), service_tier: list[str] = Query(None),
               context_window: list[str] = Query(None)):
    scp = scope.current_scope(request)
    s, e = parse_range(start, end)
    expr = _metric_expr(metric)
    gcol = safe_group_column(group_by, GROUPS)
    fcl, fp = _filters(workspace_id, api_key_id, model, service_tier, context_window)
    scl, sp = scope.usage_scope_clause(scp)
    if scl:
        fcl.append(scl); fp.extend(sp)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        if gcol:
            sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
            rows = con.execute(
                f"""SELECT {DAY_EXPR} AS day, COALESCE({gcol}, %s) AS grp, SUM({expr}) AS val
                    FROM usage_facts WHERE {where} GROUP BY day, grp ORDER BY day""",
                [sentinel, s, e, *fp],
            ).fetchall()
        else:
            rows = con.execute(
                f"""SELECT {DAY_EXPR} AS day, 'Összes' AS grp, SUM({expr}) AS val
                    FROM usage_facts WHERE {where} GROUP BY day ORDER BY day""",
                [s, e, *fp],
            ).fetchall()
    days, ordered = assemble_timeseries([dict(r) for r in rows])
    labels = resolve_labels(group_by, [g for g, _ in ordered]) if gcol else {}
    series = [{"key": g, "label": labels.get(g, g), "data": vals} for g, vals in ordered]
    return {"labels": days, "series": series, "metric": metric, "group_by": group_by}


@router.get("/breakdown")
def breakdown(request: Request, start: str, end: str, group_by: str = "model",
              metric: str = "total_tokens",
              workspace_id: list[str] = Query(None), api_key_id: list[str] = Query(None),
              model: list[str] = Query(None), service_tier: list[str] = Query(None),
              context_window: list[str] = Query(None)):
    scp = scope.current_scope(request)
    s, e = parse_range(start, end)
    expr = _metric_expr(metric)
    gcol = safe_group_column(group_by, GROUPS)
    if not gcol:
        raise HTTPException(400, "A breakdown-hoz group_by kötelező")
    sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
    fcl, fp = _filters(workspace_id, api_key_id, model, service_tier, context_window)
    scl, sp = scope.usage_scope_clause(scp)
    if scl:
        fcl.append(scl); fp.extend(sp)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        rows = con.execute(
            f"""SELECT COALESCE({gcol}, %s) AS grp, SUM({expr}) AS val
                FROM usage_facts WHERE {where} GROUP BY grp ORDER BY val DESC""",
            [sentinel, s, e, *fp],
        ).fetchall()
    labels = resolve_labels(group_by, [r["grp"] for r in rows])
    return [{"key": r["grp"], "label": labels.get(r["grp"], r["grp"]),
             "value": float(r["val"] or 0)} for r in rows]


@router.get("/cache-breakdown")
def cache_breakdown(request: Request, start: str, end: str,
                    workspace_id: list[str] = Query(None), api_key_id: list[str] = Query(None),
                    model: list[str] = Query(None), service_tier: list[str] = Query(None),
                    context_window: list[str] = Query(None)):
    """Cache-token bontás modellenként (token-szemlélet) + találati arány — optimalizáláshoz."""
    scp = scope.current_scope(request)
    s, e = parse_range(start, end)
    fcl, fp = _filters(workspace_id, api_key_id, model, service_tier, context_window)
    scl, sp = scope.usage_scope_clause(scp)
    if scl:
        fcl.append(scl); fp.extend(sp)
    # bucket_width='1d': csak napi sorokat összegzünk (jelenleg csak ilyen van, de
    # robusztus marad, ha valaha al-napi bucketek is tárolásra kerülnének).
    where = "bucket_width = '1d' AND bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        rows = con.execute(
            f"""SELECT COALESCE(model, %s) AS model,
                       COALESCE(SUM(cache_read_input_tokens), 0) AS cache_read,
                       COALESCE(SUM(cache_creation_5m_tokens), 0) AS cache_write_5m,
                       COALESCE(SUM(cache_creation_1h_tokens), 0) AS cache_write_1h,
                       COALESCE(SUM(uncached_input_tokens), 0) AS uncached_input
                FROM usage_facts WHERE {where}
                GROUP BY model
                ORDER BY (COALESCE(SUM(cache_read_input_tokens),0) + COALESCE(SUM(uncached_input_tokens),0)) DESC""",
            [GROUP_SENTINELS["model"], s, e, *fp],
        ).fetchall()
    out = []
    for r in rows:
        cache_read = int(r["cache_read"])
        uncached = int(r["uncached_input"])
        denom = cache_read + uncached
        out.append({
            "model": r["model"],
            "cache_read": cache_read,
            "cache_write_5m": int(r["cache_write_5m"]),
            "cache_write_1h": int(r["cache_write_1h"]),
            "uncached_input": uncached,
            "cache_hit_ratio": (cache_read / denom) if denom else 0.0,
        })
    return out
