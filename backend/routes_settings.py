"""Globális beállítások (ütemező-konfig stb.) — admin."""
from dateutil import parser as dtparser
from fastapi import APIRouter, Request, HTTPException

import auth
import settings_service
from database import DEFAULT_SETTINGS
from activity_logger import log_activity
from schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Tipizált kulcsok: a generikus PUT-on érkező értéket itt validáljuk, hogy később ne
# robbanjon néma 500-zal (pl. _compute_balance float()-ja). NO FALLBACK: explicit 400.
_NONNEG_NUMBER_KEYS = {"pricing.web_search_usd_per_request", "balance.anchor_usd"}
_TS_KEYS = {"balance.anchor_ts"}


def _validate_setting(key, value):
    if key in _NONNEG_NUMBER_KEYS:
        if key == "balance.anchor_usd" and value is None:
            return  # az egyenleg "nincs beállítva" állapota
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise HTTPException(400, f"A(z) {key} értéke nem-negatív szám kell legyen")
    elif key in _TS_KEYS:
        if value is None:
            return
        if not isinstance(value, str):
            raise HTTPException(400, f"A(z) {key} ISO időbélyeg-string kell legyen")
        try:
            dtparser.isoparse(value)  # szigorú: a tartományon kívüli/szemetes érték itt elbukik
        except (ValueError, OverflowError):
            raise HTTPException(400, f"A(z) {key} érvénytelen ISO időbélyeg")


@router.get("")
def get_settings(request: Request):
    auth.get_current_admin(request)
    return settings_service.get_all_settings()


@router.put("")
def update_settings(req: SettingsUpdate, request: Request):
    a = auth.get_current_admin(request)
    allowed = set(DEFAULT_SETTINGS.keys())
    filtered = {k: v for k, v in (req.values or {}).items() if k in allowed}
    if not filtered:
        raise HTTPException(400, "Nincs érvényes beállítás a kérésben")
    for k, v in filtered.items():
        _validate_setting(k, v)
    settings_service.set_many(filtered)
    log_activity(a["id"], "settings_update", detail={"keys": list(filtered.keys())})
    return settings_service.get_all_settings()
