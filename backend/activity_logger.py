"""
Tevékenységnapló (audit trail) — minden jelentős művelet az activity_log táblába.
A referenciaprojekt activity_logger.py egyszerűsített változata (nincs csoport).
"""
import json

from database import get_db, now_iso


def log_activity(
    user_id,
    action: str,
    target_type: str = None,
    target_id: str = None,
    detail: dict = None,
    ip: str = None,
) -> int:
    detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    with get_db() as con:
        row = con.execute(
            """INSERT INTO activity_log
               (user_id, action, target_type, target_id, detail, ip, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (user_id, action, target_type, target_id, detail_json, ip, now_iso()),
        ).fetchone()
    return row["id"]


def _parse_detail(row: dict) -> dict:
    detail_parsed = None
    if row.get("detail"):
        try:
            detail_parsed = json.loads(row["detail"])
        except (ValueError, TypeError):
            detail_parsed = None
    row["detail_parsed"] = detail_parsed
    return row


def _build_filters(user_id=None, action=None, date_from=None, date_to=None):
    conditions, params = [], []
    if user_id is not None:
        conditions.append("al.user_id = %s")
        params.append(user_id)
    if action:
        conditions.append("al.action = %s")
        params.append(action)
    if date_from:
        conditions.append("al.created_at >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("al.created_at <= %s")
        params.append(date_to)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def query_activity_log(user_id=None, action=None, date_from=None, date_to=None,
                       limit: int = 50, offset: int = 0) -> list:
    where, params = _build_filters(user_id, action, date_from, date_to)
    with get_db() as con:
        rows = con.execute(
            f"""SELECT al.*, u.name AS user_name, u.email AS user_email, u.role AS user_role
                FROM activity_log al
                LEFT JOIN users u ON al.user_id = u.id
                {where}
                ORDER BY al.created_at DESC
                LIMIT %s OFFSET %s""",
            (*params, limit, offset),
        ).fetchall()
    return [_parse_detail(dict(r)) for r in rows]


def get_activity_count(user_id=None, action=None, date_from=None, date_to=None) -> int:
    where, params = _build_filters(user_id, action, date_from, date_to)
    with get_db() as con:
        row = con.execute(
            f"SELECT COUNT(*) AS cnt FROM activity_log al {where}", params
        ).fetchone()
    return row["cnt"]
