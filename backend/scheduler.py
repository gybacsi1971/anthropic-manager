"""
Háttér-ütemező: asyncio ciklus, amely forrásonként konfigurálható intervallummal
hívja a collector.run_sync-et. A run_sync advisory lock-ja véd a manuális
triggerekkel való átfedéstől.

FONTOS: az alkalmazás 1 uvicorn worker-rel fut (l. Dockerfile), így egyetlen
ütemező-példány létezik. A collector advisory lock-ja akkor is biztosít, ha a
jövőben több worker indulna.
"""
import asyncio
import logging
from datetime import datetime, timezone

import admin_key_service
import collector
from settings_service import get_setting


logger = logging.getLogger("scheduler")

_task = None
_last_run: dict[str, datetime] = {}

# Sorrend: előbb a metaadat (workspace-nevek), aztán a riportok.
SOURCES = ["metadata", "usage", "cost", "claude_code"]
INTERVAL_KEYS = {
    "usage": "scheduler.usage_interval_min",
    "cost": "scheduler.cost_interval_min",
    "claude_code": "scheduler.claude_code_interval_min",
    "metadata": "scheduler.metadata_interval_min",
}
TICK_SECONDS = 60


async def _tick():
    try:
        enabled = bool(get_setting("scheduler.enabled"))
    except Exception as e:
        logger.warning("Ütemező beállítás-hiba: %s", e)
        return
    if not enabled:
        return
    if not admin_key_service.has_active_key():
        return  # nincs aktív Admin kulcs — csendben kihagyjuk

    now = datetime.now(timezone.utc)
    for source in SOURCES:
        try:
            interval_min = int(get_setting(INTERVAL_KEYS[source]))
        except Exception:
            continue
        last = _last_run.get(source)
        if last is not None and (now - last).total_seconds() < interval_min * 60:
            continue
        _last_run[source] = now
        try:
            result = await asyncio.to_thread(collector.run_sync, source, "scheduler")
            logger.info("Ütemezett sync %s: %s", source, result)
        except Exception as e:
            logger.error("Ütemezett sync %s hiba: %s", source, e)


async def _loop():
    logger.info("Ütemező elindult")
    await asyncio.sleep(5)  # DB/séma felállásának ideje
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Ütemező tick hiba: %s", e)
        await asyncio.sleep(TICK_SECONDS)


def start_scheduler():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
    return _task


def stop_scheduler():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
