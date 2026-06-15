"""Cost (USD költség) analitika a helyi cost_facts táblából.

Az `amount_cents` az Admin API nyers, centben kifejezett értéke; a USD = cent / 100.
"""
from fastapi import APIRouter, Request, Query, HTTPException

import scope
import settings_service
from database import get_db
from pricing_service import EST_USD_EXPR as _EST_USD, PRICE_JOIN_LATERAL as _PRICE_JOIN, WS_PRICE_SETTING
from query_helpers import (
    parse_range, safe_group_column, assemble_timeseries, resolve_labels, GROUP_SENTINELS,
)

router = APIRouter(prefix="/api/cost", tags=["cost"])

# A cost amount centben van; USD-re a SUM-ot 100-zal osztjuk.
USD = "SUM(amount_cents) / 100.0"
GROUPS = {
    "workspace_id": "workspace_id",
    "model": "model",
    "cost_type": "cost_type",
    "token_type": "token_type",
    "service_tier": "service_tier",
    "context_window": "context_window",
    "description": "description",
}
DAY_EXPR = "to_char(date_trunc('day', bucket_start AT TIME ZONE 'UTC'), 'YYYY-MM-DD')"

# A becsült költséghez használható (usage_facts-ban létező) csoportosító dimenziók.
# FONTOS: az api_key_id CSAK itt szerepel, a GROUPS-ban NEM — a tényleges cost_facts
# táblában nincs api_key_id oszlop, így kulcsra valódi (tényleges) bontás lehetetlen.
# Kulcsra tehát kizárólag BECSÜLT költség adható (usage_facts × árlista).
USAGE_GROUPS = {
    "model": "model",
    "workspace_id": "workspace_id",
    "service_tier": "service_tier",
    "context_window": "context_window",
    "api_key_id": "api_key_id",
}

def _filters(workspace_id, model, cost_type, service_tier):
    clauses, params = [], []
    for col, vals in (
        ("workspace_id", workspace_id), ("model", model),
        ("cost_type", cost_type), ("service_tier", service_tier),
    ):
        if vals:
            clauses.append(f"{col} = ANY(%s)")
            params.append(list(vals))
    return clauses, params


def _usage_filters(workspace_id, model, service_tier):
    """A cost-szűrőkből a usage_facts-ra értelmezhetők (a cost_type nem az)."""
    clauses, params = [], []
    for col, vals in (("workspace_id", workspace_id), ("model", model), ("service_tier", service_tier)):
        if vals:
            clauses.append(f"uf.{col} = ANY(%s)")
            params.append(list(vals))
    return clauses, params


def _forbid_viewer(request):
    """A tisztán TÉNYLEGES (cost_facts) végpontok nézőnek tiltottak.

    A tényleges költség csak workspace+description szerint van bontva (api_key_id nincs),
    így kulcsra nem szűrhető → a viewer adatköre csak a BECSÜLT költség (kombinált idősor).
    """
    scp = scope.current_scope(request)  # 401, ha nincs bejelentkezés
    if scp is not None:
        raise HTTPException(
            403, "A tényleges költség nézőként nem érhető el (kulcsra nem szűrhető); "
                 "a becsült költséget a kombinált idősor mutatja.")


@router.get("/summary")
def summary(request: Request, start: str, end: str,
            workspace_id: list[str] = Query(None), model: list[str] = Query(None),
            cost_type: list[str] = Query(None), service_tier: list[str] = Query(None)):
    _forbid_viewer(request)
    s, e = parse_range(start, end)
    fcl, fp = _filters(workspace_id, model, cost_type, service_tier)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        row = con.execute(
            f"SELECT COALESCE({USD}, 0) AS total_usd, COUNT(*) AS rows FROM cost_facts WHERE {where}",
            [s, e, *fp],
        ).fetchone()
        by_type = con.execute(
            f"""SELECT COALESCE(cost_type, '(nincs)') AS cost_type, COALESCE({USD},0) AS usd
                FROM cost_facts WHERE {where} GROUP BY cost_type ORDER BY usd DESC""",
            [s, e, *fp],
        ).fetchall()
    return {
        "total_usd": float(row["total_usd"] or 0),
        "by_cost_type": [{"cost_type": r["cost_type"], "usd": float(r["usd"] or 0)} for r in by_type],
    }


@router.get("/timeseries")
def timeseries(request: Request, start: str, end: str, group_by: str = "none",
               workspace_id: list[str] = Query(None), model: list[str] = Query(None),
               cost_type: list[str] = Query(None), service_tier: list[str] = Query(None)):
    _forbid_viewer(request)
    s, e = parse_range(start, end)
    gcol = safe_group_column(group_by, GROUPS)
    fcl, fp = _filters(workspace_id, model, cost_type, service_tier)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        if gcol:
            sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
            rows = con.execute(
                f"""SELECT {DAY_EXPR} AS day, COALESCE({gcol}, %s) AS grp, {USD} AS val
                    FROM cost_facts WHERE {where} GROUP BY day, grp ORDER BY day""",
                [sentinel, s, e, *fp],
            ).fetchall()
        else:
            rows = con.execute(
                f"""SELECT {DAY_EXPR} AS day, 'Összes' AS grp, {USD} AS val
                    FROM cost_facts WHERE {where} GROUP BY day ORDER BY day""",
                [s, e, *fp],
            ).fetchall()
    days, ordered = assemble_timeseries([dict(r) for r in rows])
    labels = resolve_labels(group_by, [g for g, _ in ordered]) if gcol else {}
    series = [{"key": g, "label": labels.get(g, g), "data": vals} for g, vals in ordered]
    return {"labels": days, "series": series, "group_by": group_by, "unit": "USD"}


@router.get("/breakdown")
def breakdown(request: Request, start: str, end: str, group_by: str = "model",
              workspace_id: list[str] = Query(None), model: list[str] = Query(None),
              cost_type: list[str] = Query(None), service_tier: list[str] = Query(None)):
    _forbid_viewer(request)
    s, e = parse_range(start, end)
    gcol = safe_group_column(group_by, GROUPS)
    if not gcol:
        raise HTTPException(400, "A breakdown-hoz group_by kötelező")
    sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
    fcl, fp = _filters(workspace_id, model, cost_type, service_tier)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))
    with get_db() as con:
        rows = con.execute(
            f"""SELECT COALESCE({gcol}, %s) AS grp, {USD} AS val
                FROM cost_facts WHERE {where} GROUP BY grp ORDER BY val DESC""",
            [sentinel, s, e, *fp],
        ).fetchall()
    labels = resolve_labels(group_by, [r["grp"] for r in rows])
    return [{"key": r["grp"], "label": labels.get(r["grp"], r["grp"]),
             "value": float(r["val"] or 0)} for r in rows]


@router.get("/combined-timeseries")
def combined_timeseries(request: Request, start: str, end: str, group_by: str = "model",
                        workspace_id: list[str] = Query(None), model: list[str] = Query(None),
                        cost_type: list[str] = Query(None), service_tier: list[str] = Query(None)):
    """Tényleges (cost_facts) + becsült (usage_facts × árlista) napi idősor egyben.

    Egy adott napra a TÉNYLEGES költséget mutatjuk, ha a Cost API már lezárta a napot
    (van cost_facts sor). Ahol nincs (pl. a mai, még folyó nap), a BECSÜLT költséget
    tesszük be helyette (a frontend halványabb oszloppal jelöli). A becslés a
    leghosszabb-prefix árillesztésen alapul; az árazatlan modelleket külön jelezzük
    (nem nyeljük el csendben 0-ként).
    """
    scp = scope.current_scope(request)
    viewer = scp is not None  # nézőnek nincs tényleges költség, csak becsült (scope-olt)
    s, e = parse_range(start, end)
    # A group_by-t az EGYESÍTETT (tényleges ∪ becsült) whitelistre validáljuk: az
    # api_key_id csak BECSÜLT dimenzió (a cost_facts-ban nincs ilyen oszlop), ezért a
    # GROUPS önmagában 400-at adna rá. A validációs hívást meg KELL tartani (érvénytelen
    # group_by → 400, SQL-injection védelem).
    safe_group_column(group_by, {**GROUPS, **USAGE_GROUPS})
    # A TÉNYLEGES (cost_facts) ág KIZÁRÓLAG valódi cost_facts oszlopra futhat:
    actual_gcol = GROUPS.get(group_by)  # None, ha csak-becsült dim (api_key_id) vagy 'none'
    fcl, fp = _filters(workspace_id, model, cost_type, service_tier)
    where = "bucket_start >= %s AND bucket_start < %s" + ("".join(" AND " + c for c in fcl))

    with get_db() as con:
        # --- TÉNYLEGES (cost_facts) — nézőnek kihagyjuk (kulcsra nem szűrhető) ---
        if viewer:
            actual_rows = []
        elif actual_gcol:
            sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
            actual_rows = [dict(r) for r in con.execute(
                f"""SELECT {DAY_EXPR} AS day, COALESCE({actual_gcol}, %s) AS grp, {USD} AS val
                    FROM cost_facts WHERE {where} GROUP BY day, grp ORDER BY day""",
                [sentinel, s, e, *fp],
            ).fetchall()]
        elif group_by in ("none", ""):
            actual_rows = [dict(r) for r in con.execute(
                f"""SELECT {DAY_EXPR} AS day, 'Összes' AS grp, {USD} AS val
                    FROM cost_facts WHERE {where} GROUP BY day ORDER BY day""",
                [s, e, *fp],
            ).fetchall()]
        else:
            # Csak-becsült dimenzió (pl. api_key_id): a cost_facts-ban nincs ilyen oszlop,
            # így TÉNYLEGES bontás nem létezik → üres; a teljes idősort a becsült ág adja.
            actual_rows = []
        actual_day_set = {r["day"] for r in actual_rows}

        # --- BECSÜLT (usage_facts × árlista) — csak usage-kompatibilis bontásban ---
        estimate_supported = (group_by in USAGE_GROUPS) or (group_by == "none")
        est_rows, unpriced = [], []
        if estimate_supported:
            ws_price = float(settings_service.get_setting(WS_PRICE_SETTING))
            ucol = USAGE_GROUPS.get(group_by)  # None, ha group_by == 'none'
            ufcl, ufp = _usage_filters(workspace_id, model, service_tier)
            uscl, usp = scope.usage_scope_clause(scp, prefix="uf.")  # nézőnek scope-szűrés
            if uscl:
                ufcl.append(uscl); ufp.extend(usp)
            uwhere = ("uf.bucket_width = '1d' AND uf.bucket_start >= %s AND uf.bucket_start < %s"
                      + "".join(" AND " + c for c in ufcl))
            uf_day = DAY_EXPR.replace("bucket_start", "uf.bucket_start")
            if ucol:
                sentinel = GROUP_SENTINELS.get(group_by, "(nincs)")
                est_rows = con.execute(
                    f"""SELECT {uf_day} AS day, COALESCE(uf.{ucol}, %s) AS grp, SUM({_EST_USD}) AS val
                        FROM usage_facts uf {_PRICE_JOIN}
                        WHERE {uwhere} GROUP BY day, grp ORDER BY day""",
                    [sentinel, ws_price, s, e, *ufp],
                ).fetchall()
            else:
                est_rows = con.execute(
                    f"""SELECT {uf_day} AS day, 'Összes' AS grp, SUM({_EST_USD}) AS val
                        FROM usage_facts uf {_PRICE_JOIN}
                        WHERE {uwhere} GROUP BY day ORDER BY day""",
                    [ws_price, s, e, *ufp],
                ).fetchall()
            est_rows = [dict(r) for r in est_rows]
            # Árazatlan modellek (van forgalom, de nincs illeszkedő árminta) — figyelmeztetés.
            unpriced = [r["model"] for r in con.execute(
                f"""SELECT DISTINCT uf.model FROM usage_facts uf
                    WHERE {uwhere} AND uf.model IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM model_pricing mp WHERE starts_with(uf.model, mp.model_pattern))
                      AND (uf.uncached_input_tokens + uf.output_tokens + uf.cache_read_input_tokens
                           + uf.cache_creation_5m_tokens + uf.cache_creation_1h_tokens) > 0
                    ORDER BY uf.model""",
                [s, e, *ufp],
            ).fetchall()]

    # --- MERGE: naponként a tényleges nyer; ahol nincs, a becsült tölti ki ---
    est_by_key, est_days = {}, set()
    for r in est_rows:
        est_days.add(r["day"])
        est_by_key[(r["day"], r["grp"])] = float(r["val"] or 0)
    used_est_days = {d for d in est_days if d not in actual_day_set}
    all_days = sorted(actual_day_set | used_est_days)
    estimated_flags = [d not in actual_day_set for d in all_days]

    groups: dict = {}
    for r in actual_rows:
        groups.setdefault(r["grp"], {})[r["day"]] = float(r["val"] or 0)
    for (day, grp), val in est_by_key.items():
        if day in actual_day_set:
            continue  # ezen a napon a tényleges adat az irányadó
        groups.setdefault(grp, {})[day] = val

    pairs = [(grp, [daymap.get(d, 0.0) for d in all_days]) for grp, daymap in groups.items()]
    pairs.sort(key=lambda kv: sum(kv[1]), reverse=True)
    labels = resolve_labels(group_by, [g for g, _ in pairs]) if group_by != "none" else {}
    series = [{"key": g, "label": labels.get(g, g), "data": vals} for g, vals in pairs]

    estimated_total = sum(
        v for _, vals in pairs for i, v in enumerate(vals) if estimated_flags[i]
    )
    return {
        "labels": all_days,
        "series": series,
        "estimated_days": estimated_flags,
        "estimate_supported": estimate_supported,
        "estimated_total_usd": estimated_total,
        "unpriced_models": unpriced,
        "group_by": group_by,
        "unit": "USD",
    }


@router.get("/cache-savings")
def cache_savings(request: Request, start: str, end: str,
                  workspace_id: list[str] = Query(None), model: list[str] = Query(None),
                  service_tier: list[str] = Query(None)):
    """Prompt-cache hatékonyság a token-forgalomból + árlistából (költségoptimalizáláshoz).

    - cache-olvasás megtakarítás: a cache-read token a beviteli ár 10%-áért megy, így a
      megtakarítás = cache_read × (input_ár − cache_read_ár).
    - cache-írás felár: a cache-write token a beviteli ár 1.25× (5m) / 2× (1h) áráért megy,
      a felár = write_token × (write_ár − input_ár).
    - nettó cache-haszon = olvasás-megtakarítás − írás-felár.
    A token-összegek a teljes forgalomra vonatkoznak; a USD-számok csak az árazott modellekre.
    """
    scp = scope.current_scope(request)
    s, e = parse_range(start, end)
    ufcl, ufp = _usage_filters(workspace_id, model, service_tier)
    uscl, usp = scope.usage_scope_clause(scp, prefix="uf.")  # nézőnek scope-szűrés
    if uscl:
        ufcl.append(uscl); ufp.extend(usp)
    uwhere = ("uf.bucket_width = '1d' AND uf.bucket_start >= %s AND uf.bucket_start < %s"
              + "".join(" AND " + c for c in ufcl))
    # LEFT JOIN: a token-összegek az árazatlan modellekre is teljesek, a USD csak az árazottakra.
    left_join = _PRICE_JOIN.replace("JOIN LATERAL", "LEFT JOIN LATERAL", 1)
    with get_db() as con:
        row = con.execute(
            f"""SELECT
                    COALESCE(SUM(uf.cache_read_input_tokens), 0) AS cache_read_tokens,
                    COALESCE(SUM(uf.uncached_input_tokens), 0) AS uncached_input_tokens,
                    COALESCE(SUM(uf.cache_creation_5m_tokens + uf.cache_creation_1h_tokens), 0) AS cache_write_tokens,
                    COALESCE(SUM(uf.cache_read_input_tokens * (p.input_usd_per_mtok - p.cache_read_usd_per_mtok)) / 1000000.0, 0) AS read_savings_usd,
                    COALESCE(SUM((uf.cache_creation_5m_tokens * (p.cache_write_5m_usd_per_mtok - p.input_usd_per_mtok)
                               + uf.cache_creation_1h_tokens * (p.cache_write_1h_usd_per_mtok - p.input_usd_per_mtok))) / 1000000.0, 0) AS write_overhead_usd,
                    COALESCE(SUM(uf.cache_read_input_tokens * p.cache_read_usd_per_mtok) / 1000000.0, 0) AS read_cost_usd,
                    COALESCE(SUM((uf.cache_creation_5m_tokens * p.cache_write_5m_usd_per_mtok
                               + uf.cache_creation_1h_tokens * p.cache_write_1h_usd_per_mtok)) / 1000000.0, 0) AS write_cost_usd
                FROM usage_facts uf {left_join}
                WHERE {uwhere}""",
            [s, e, *ufp],
        ).fetchone()
    r = dict(row)
    cache_read = int(r["cache_read_tokens"])
    uncached = int(r["uncached_input_tokens"])
    denom = cache_read + uncached
    read_savings = float(r["read_savings_usd"] or 0)
    write_overhead = float(r["write_overhead_usd"] or 0)
    return {
        "cache_read_tokens": cache_read,
        "uncached_input_tokens": uncached,
        "cache_write_tokens": int(r["cache_write_tokens"]),
        "cache_hit_ratio": (cache_read / denom) if denom else 0.0,
        "read_savings_usd": read_savings,
        "write_overhead_usd": write_overhead,
        "net_benefit_usd": read_savings - write_overhead,
        "read_cost_usd": float(r["read_cost_usd"] or 0),
        "write_cost_usd": float(r["write_cost_usd"] or 0),
    }
