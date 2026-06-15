"""
Szervezet egyenlege — KÉZI HORGONY, PONTOS IDŐPONTTAL.

Az Anthropic Admin API NEM ad vissza kredit-egyenleget (csak a Console Billing oldal
mutatja). Ezért az admin beír egy egyenleget egy PONTOS IDŐPONTTAL (a horgonyt), és az
app a horgony időpontjától vonja le a felmerült költséget:

  - a horgony NAPJÁN csak a horgony időpontja UTÁNI órák becsült költsége (usage_hourly_facts),
  - a horgony napja UTÁNI teljes, lezárt napok TÉNYLEGES költsége (cost_facts),
  - a még nyitott teljes napok (pl. ma, ha a horgony korábbi nap) BECSÜLT költsége (usage_facts 1d).

Ha a horgony napjára nincs órás adat (pl. régi horgony), a nap költségét időarányosan
becsüljük (prorate, `anchor_day_prorated=true` jelzéssel).

A kapott egyenleg becslés — a Cost API a priority tier költségét nem adja, és kerekítés
is van —, ezért érdemes időnként újraszinkronizálni a Console valós számával.
"""
from datetime import datetime, timedelta, timezone

from dateutil import parser as dtparser
from fastapi import APIRouter, Request, HTTPException

import auth
import settings_service
from database import get_db
from pricing_service import EST_USD_EXPR, PRICE_JOIN_LATERAL, WS_PRICE_SETTING
from activity_logger import log_activity
from schemas import BalanceUpdate

router = APIRouter(prefix="/api/balance", tags=["balance"])

_DAY_COST = "to_char(date_trunc('day', bucket_start AT TIME ZONE 'UTC'), 'YYYY-MM-DD')"


def _midnight_iso(d) -> str:
    return d.isoformat() + "T00:00:00Z"


def _compute_balance() -> dict:
    anchor_usd = settings_service.get_setting("balance.anchor_usd")
    anchor_ts = settings_service.get_setting("balance.anchor_ts")
    out = {
        "anchor_usd": anchor_usd,
        "anchor_ts": anchor_ts,
        "configured": anchor_usd is not None and anchor_ts is not None,
        "actual_spent_usd": 0.0,
        "estimated_open_usd": 0.0,
        "spent_usd": 0.0,
        "balance_usd": None,
        "unpriced_models": [],
        "anchor_day_prorated": False,
    }
    if not out["configured"]:
        return out

    # Defenzív: ha a tárolt érték valamiért korrupt, ne 500-azzon az olvasó végpont —
    # kezeljük "nincs beállítva"-ként (az írási utak amúgy validálnak).
    try:
        anchor_dt = dtparser.isoparse(anchor_ts)
    except (ValueError, OverflowError):
        out["configured"] = False
        return out
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
    anchor_dt = anchor_dt.astimezone(timezone.utc)
    anchor_day = anchor_dt.date()
    day0 = _midnight_iso(anchor_day)                     # horgony napjának 00:00Z
    day1_date = anchor_day + timedelta(days=1)
    day1 = _midnight_iso(day1_date)                      # rákövetkező nap 00:00Z
    day1_dt = datetime(day1_date.year, day1_date.month, day1_date.day, tzinfo=timezone.utc)
    ws_price = float(settings_service.get_setting(WS_PRICE_SETTING))

    with get_db() as con:
        # 1) Teljes, lezárt napok a horgony napja UTÁN: tényleges (cost_facts).
        actual = con.execute(
            f"""SELECT {_DAY_COST} AS day, SUM(amount_cents) / 100.0 AS usd
                FROM cost_facts WHERE bucket_start >= %s GROUP BY day""",
            [day1],
        ).fetchall()
        actual_total = sum(float(r["usd"] or 0) for r in actual)
        actual_days = {r["day"] for r in actual}

        # 2) Teljes nyitott napok a horgony napja után (pl. ma): becsült 1d, ahol nincs cost_facts.
        est_full = con.execute(
            f"""SELECT {_DAY_COST.replace('bucket_start', 'uf.bucket_start')} AS day,
                       SUM({EST_USD_EXPR}) AS usd
                FROM usage_facts uf {PRICE_JOIN_LATERAL}
                WHERE uf.bucket_width = '1d' AND uf.bucket_start >= %s GROUP BY day""",
            [ws_price, day1],
        ).fetchall()
        est_full_open = sum(float(r["usd"] or 0) for r in est_full if r["day"] not in actual_days)

        # 3) A horgony NAPJÁNAK része: a horgony időpontja utáni órák becsült költsége.
        prorated = False
        has_hourly = con.execute(
            "SELECT 1 FROM usage_hourly_facts WHERE bucket_start >= %s AND bucket_start < %s LIMIT 1",
            [day0, day1],
        ).fetchone() is not None
        if has_hourly:
            # Pontos időhöz: a horgony ÓRÁJÁT (amibe a horgony esik) az óra post-anchor
            # törtjével arányosítjuk, az utána eső teljes órákat 1.0 súllyal számoljuk.
            hour_floor = anchor_dt.replace(minute=0, second=0, microsecond=0)
            next_hour = hour_floor + timedelta(hours=1)
            boundary_fraction = max(0.0, min(1.0, (next_hour - anchor_dt).total_seconds() / 3600.0))
            full = con.execute(
                f"""SELECT COALESCE(SUM({EST_USD_EXPR}), 0) AS usd
                    FROM usage_hourly_facts uf {PRICE_JOIN_LATERAL}
                    WHERE uf.bucket_start >= %s AND uf.bucket_start < %s""",
                [ws_price, next_hour.isoformat(), day1],
            ).fetchone()
            boundary = con.execute(
                f"""SELECT COALESCE(SUM({EST_USD_EXPR}), 0) AS usd
                    FROM usage_hourly_facts uf {PRICE_JOIN_LATERAL}
                    WHERE uf.bucket_start >= %s AND uf.bucket_start < %s""",
                [ws_price, hour_floor.isoformat(), next_hour.isoformat()],
            ).fetchone()
            anchor_partial = float(full["usd"] or 0) + boundary_fraction * float(boundary["usd"] or 0)
        else:
            # Nincs órás adat a horgony napjára (pl. régi horgony) → időarányos becslés.
            prorated = True
            base_row = con.execute(
                f"""SELECT COALESCE(SUM(amount_cents) / 100.0, 0) AS usd
                    FROM cost_facts WHERE bucket_start >= %s AND bucket_start < %s""",
                [day0, day1],
            ).fetchone()
            base = float(base_row["usd"] or 0)
            if base == 0:  # a horgony napja még nincs lezárva → becsült 1d
                est_row = con.execute(
                    f"""SELECT COALESCE(SUM({EST_USD_EXPR}), 0) AS usd
                        FROM usage_facts uf {PRICE_JOIN_LATERAL}
                        WHERE uf.bucket_width = '1d' AND uf.bucket_start >= %s AND uf.bucket_start < %s""",
                    [ws_price, day0, day1],
                ).fetchone()
                base = float(est_row["usd"] or 0)
            fraction = max(0.0, min(1.0, (day1_dt - anchor_dt).total_seconds() / 86400.0))
            anchor_partial = base * fraction

        # Árazatlan modellek a becsült (horgony napi + nyitott) ablakban — az egyenleg optimista lehet.
        unpriced = [r["model"] for r in con.execute(
            f"""SELECT DISTINCT uf.model FROM usage_facts uf
                WHERE uf.bucket_width = '1d' AND uf.bucket_start >= %s
                  AND uf.model IS NOT NULL
                  AND NOT ({_DAY_COST.replace('bucket_start', 'uf.bucket_start')} = ANY(%s))
                  AND NOT EXISTS (
                      SELECT 1 FROM model_pricing mp WHERE starts_with(uf.model, mp.model_pattern))
                  AND (uf.uncached_input_tokens + uf.output_tokens + uf.cache_read_input_tokens
                       + uf.cache_creation_5m_tokens + uf.cache_creation_1h_tokens) > 0
                ORDER BY uf.model""",
            [day0, list(actual_days)],
        ).fetchall()]

    estimated = est_full_open + anchor_partial
    spent = actual_total + estimated
    out.update(
        actual_spent_usd=actual_total,
        estimated_open_usd=estimated,
        spent_usd=spent,
        balance_usd=float(anchor_usd) - spent,
        unpriced_models=unpriced,
        anchor_day_prorated=prorated,
    )
    return out


@router.get("")
def get_balance(request: Request):
    # A szervezet egyenlege org-szintű (nem hatókörözhető) → csak admin.
    auth.get_current_admin(request)
    return _compute_balance()


@router.put("")
def set_balance(req: BalanceUpdate, request: Request):
    a = auth.get_current_admin(request)
    try:
        dt = dtparser.isoparse(req.anchor_ts)
    except (ValueError, OverflowError):
        raise HTTPException(400, "Érvénytelen időbélyeg (várt: ISO 8601)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    anchor_ts = dt.astimezone(timezone.utc).isoformat()
    settings_service.set_many({
        "balance.anchor_usd": req.amount_usd,
        "balance.anchor_ts": anchor_ts,
    })
    log_activity(a["id"], "balance_update", target_type="balance",
                 detail={"amount_usd": req.amount_usd, "anchor_ts": anchor_ts})
    return _compute_balance()
