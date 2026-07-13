"""Claude Code analitika a helyi claude_code_facts táblából (napi, aktoronként)."""
from fastapi import APIRouter, Request, HTTPException

import scope
from database import get_db
from query_helpers import parse_date_range, day_range, assemble_timeseries

router = APIRouter(prefix="/api/claude-code", tags=["claude-code"])

ACTOR = "COALESCE(actor_email, actor_api_key_name, '(ismeretlen)')"
METRICS = {
    "sessions": "num_sessions",
    "lines_added": "lines_added",
    "lines_removed": "lines_removed",
    "commits": "commits",
    "pull_requests": "pull_requests",
    "cost": "estimated_cost_cents / 100.0",
}


@router.get("/summary")
def summary(request: Request, start: str, end: str):
    scp = scope.current_scope(request)
    s, e = parse_date_range(start, end)
    with get_db() as con:
        ccl, cp = scope.claude_code_scope_clause(scp, con)
        where = "day >= %s AND day < %s" + (f" AND {ccl}" if ccl else "")
        row = con.execute(
            f"""SELECT COALESCE(SUM(num_sessions),0) AS sessions,
                       COALESCE(SUM(lines_added),0) AS lines_added,
                       COALESCE(SUM(lines_removed),0) AS lines_removed,
                       COALESCE(SUM(commits),0) AS commits,
                       COALESCE(SUM(pull_requests),0) AS pull_requests,
                       COALESCE(SUM(estimated_cost_cents),0)/100.0 AS cost_usd,
                       COUNT(DISTINCT {ACTOR}) AS actors
                FROM claude_code_facts WHERE {where}""",
            [s, e, *cp],
        ).fetchone()
    d = dict(row)
    return {
        "sessions": int(d["sessions"]),
        "lines_added": int(d["lines_added"]),
        "lines_removed": int(d["lines_removed"]),
        "commits": int(d["commits"]),
        "pull_requests": int(d["pull_requests"]),
        "cost_usd": float(d["cost_usd"] or 0),
        "actors": int(d["actors"]),
    }


@router.get("/timeseries")
def timeseries(request: Request, start: str, end: str, metric: str = "sessions"):
    scp = scope.current_scope(request)
    if metric not in METRICS:
        raise HTTPException(400, f"Érvénytelen metrika: {metric}")
    expr = METRICS[metric]
    s, e = parse_date_range(start, end)
    with get_db() as con:
        ccl, cp = scope.claude_code_scope_clause(scp, con)
        where = "day >= %s AND day < %s" + (f" AND {ccl}" if ccl else "")
        rows = con.execute(
            f"""SELECT to_char(day, 'YYYY-MM-DD') AS day, 'Összes' AS grp, SUM({expr}) AS val
                FROM claude_code_facts WHERE {where} GROUP BY day ORDER BY day""",
            [s, e, *cp],
        ).fetchall()
    days, ordered = assemble_timeseries([dict(r) for r in rows], day_range(start, end))
    series = [{"key": g, "label": g, "data": vals} for g, vals in ordered]
    return {"labels": days, "series": series, "metric": metric}


@router.get("/leaderboard")
def leaderboard(request: Request, start: str, end: str, limit: int = 50):
    scp = scope.current_scope(request)
    s, e = parse_date_range(start, end)
    with get_db() as con:
        ccl, cp = scope.claude_code_scope_clause(scp, con)
        where = "day >= %s AND day < %s" + (f" AND {ccl}" if ccl else "")
        rows = con.execute(
            f"""SELECT {ACTOR} AS actor,
                       SUM(num_sessions) AS sessions,
                       SUM(lines_added) AS lines_added,
                       SUM(lines_removed) AS lines_removed,
                       SUM(commits) AS commits,
                       SUM(pull_requests) AS pull_requests,
                       SUM(estimated_cost_cents)/100.0 AS cost_usd,
                       SUM(edit_accepted + multi_edit_accepted + write_accepted + notebook_edit_accepted) AS accepted,
                       SUM(edit_rejected + multi_edit_rejected + write_rejected + notebook_edit_rejected) AS rejected
                FROM claude_code_facts WHERE {where}
                GROUP BY actor ORDER BY cost_usd DESC LIMIT %s""",
            [s, e, *cp, min(limit, 200)],
        ).fetchall()
    result = []
    for r in rows:
        accepted = int(r["accepted"] or 0)
        rejected = int(r["rejected"] or 0)
        total = accepted + rejected
        result.append({
            "actor": r["actor"],
            "sessions": int(r["sessions"] or 0),
            "lines_added": int(r["lines_added"] or 0),
            "lines_removed": int(r["lines_removed"] or 0),
            "commits": int(r["commits"] or 0),
            "pull_requests": int(r["pull_requests"] or 0),
            "cost_usd": float(r["cost_usd"] or 0),
            "accepted": accepted,
            "rejected": rejected,
            "acceptance_rate": (accepted / total) if total else None,
        })
    return result


@router.get("/acceptance")
def acceptance(request: Request, start: str, end: str):
    scp = scope.current_scope(request)
    s, e = parse_date_range(start, end)
    tools = {
        "edit": ("edit_accepted", "edit_rejected"),
        "multi_edit": ("multi_edit_accepted", "multi_edit_rejected"),
        "write": ("write_accepted", "write_rejected"),
        "notebook_edit": ("notebook_edit_accepted", "notebook_edit_rejected"),
    }
    select = ", ".join(
        f"COALESCE(SUM({a}),0) AS {a}, COALESCE(SUM({r}),0) AS {r}" for a, r in tools.values()
    )
    with get_db() as con:
        ccl, cp = scope.claude_code_scope_clause(scp, con)
        where = "day >= %s AND day < %s" + (f" AND {ccl}" if ccl else "")
        row = con.execute(
            f"SELECT {select} FROM claude_code_facts WHERE {where}", [s, e, *cp]
        ).fetchone()
    out = []
    for name, (a, r) in tools.items():
        acc = int(row[a]); rej = int(row[r]); tot = acc + rej
        out.append({
            "tool": name, "accepted": acc, "rejected": rej,
            "acceptance_rate": (acc / tot) if tot else None,
        })
    return out
