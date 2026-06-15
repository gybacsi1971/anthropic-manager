"""Tevékenységnapló lekérdezése."""
from fastapi import APIRouter, Request

import auth
from activity_logger import query_activity_log, get_activity_count

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def activity(request: Request, limit: int = 50, offset: int = 0, action: str = None):
    user = auth.get_current_user(request)
    limit = min(max(limit, 1), 200)
    # A néző csak a SAJÁT tevékenységét látja; az admin mindenkit.
    uid = None if user.get("role") == "admin" else user["id"]
    items = query_activity_log(user_id=uid, action=action, limit=limit, offset=offset)
    total = get_activity_count(user_id=uid, action=action)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
