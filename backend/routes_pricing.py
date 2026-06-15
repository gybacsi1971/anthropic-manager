"""Modell-árazás (becsült költséghez) — admin szerkesztés + hivatalos frissítés."""
from fastapi import APIRouter, Request, HTTPException

import auth
import pricing_service
from activity_logger import log_activity
from schemas import PricingUpdate

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


@router.get("")
def get_pricing(request: Request):
    auth.get_current_admin(request)
    return pricing_service.list_pricing()


@router.put("")
def put_pricing(req: PricingUpdate, request: Request):
    """A teljes árlistát menti (rács-szinkron). Az itt nem szereplő mintákat törli."""
    a = auth.get_current_admin(request)
    # A kliens által küldött provenance-t (seed/manual/official) megőrizzük — a refresh
    # után mentett sorok 'official'-ként, a kézzel felvettek 'manual'-ként maradnak.
    items = [it.model_dump() for it in req.items]
    try:
        count = pricing_service.save_pricing(items)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(a["id"], "pricing_update", target_type="model_pricing", detail={"models": count})
    return pricing_service.list_pricing()


@router.post("/refresh")
def refresh_pricing(request: Request):
    """Letölti és parse-olja a hivatalos árlistát, és VISSZAADJA felülvizsgálatra (nem ment).

    A felhasználó (admin) a rácsban átnézi a javasolt értékeket, és külön gombbal menti.
    """
    a = auth.get_current_admin(request)
    try:
        proposed = pricing_service.fetch_official_pricing()
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    log_activity(a["id"], "pricing_refresh", target_type="model_pricing",
                 detail={"source": pricing_service.PRICING_DOC_URL, "models": len(proposed)})
    return {"proposed": proposed, "source": pricing_service.PRICING_DOC_URL}
