"""
Modell-árazás kezelése a becsült költséghez.

Az árak a `model_pricing` táblában élnek (szerkeszthető, az init seedeli a jelenlegi
hivatalos értékekkel). A becsült költség = usage_facts token-darabszámok × ezen árak;
a számítást a routes_cost.py végzi SQL-ben (leghosszabb-prefix illesztés a model-re).

A "Frissítés a hivatalos árlistából" gomb a hivatalos docs oldalt tölti le és parse-olja
(stdlib HTMLParser — nincs külső függőség), majd a parse-olt értékeket VISSZAADJA
felülvizsgálatra; a mentés külön, explicit admin-művelet (review-then-apply).
"""
import re
from html.parser import HTMLParser

import httpx

from database import get_db, now_iso

# A hivatalos árlista. A nyers HTML tartalmazza a teljes táblát (nem JS-shell),
# így szerver-oldalról letölthető és parse-olható.
PRICING_DOC_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

# A web keresés kérésenkénti árának settings-kulcsa (a becsült költséghez).
WS_PRICE_SETTING = "pricing.web_search_usd_per_request"

# Becsült USD egy usage_facts sorra. Aliasok: uf = usage_facts, p = illeszkedő ár (PRICE_JOIN_LATERAL).
# A %s a web keresés kérésenkénti ára. Megosztott a routes_cost (idősor) és routes_balance (egyenleg) közt.
EST_USD_EXPR = """(
    uf.uncached_input_tokens     * p.input_usd_per_mtok
  + uf.cache_creation_5m_tokens  * p.cache_write_5m_usd_per_mtok
  + uf.cache_creation_1h_tokens  * p.cache_write_1h_usd_per_mtok
  + uf.cache_read_input_tokens   * p.cache_read_usd_per_mtok
  + uf.output_tokens             * p.output_usd_per_mtok
) / 1000000.0 + uf.web_search_requests * %s"""

# Leghosszabb-prefix árillesztés a usage_facts.model-hez (INNER — az árazatlan sorokat elejti).
PRICE_JOIN_LATERAL = """JOIN LATERAL (
    SELECT mp.* FROM model_pricing mp
    WHERE uf.model IS NOT NULL AND starts_with(uf.model, mp.model_pattern)
    ORDER BY length(mp.model_pattern) DESC
    LIMIT 1
) p ON TRUE"""

# Ár-oszlop kulcs → a fejléccímkében keresett részstringek (kisbetűsen). Az oszlopokat
# CÍMKE alapján azonosítjuk (nem pozíció szerint), így ha a docs átrendezi/beszúr egy
# oszlopot, nem mentünk csendben rossz árat (a hiányzó oszlop inkább hibát ad).
_HEADER_PATTERNS = [
    ("input_usd_per_mtok",          ("base input",)),
    ("cache_write_5m_usd_per_mtok", ("5m", "5 min", "5-min", "5 minute")),
    ("cache_write_1h_usd_per_mtok", ("1h", "1 hour", "1-hour", "1 hr")),
    ("cache_read_usd_per_mtok",     ("cache hit", "cache read", "refresh")),
    ("output_usd_per_mtok",         ("output",)),
]


# ================================================================
# Olvasás / írás
# ================================================================

def _to_item(row) -> dict:
    return {
        "model_pattern": row["model_pattern"],
        "display_name": row["display_name"],
        "input_usd_per_mtok": float(row["input_usd_per_mtok"]),
        "cache_write_5m_usd_per_mtok": float(row["cache_write_5m_usd_per_mtok"]),
        "cache_write_1h_usd_per_mtok": float(row["cache_write_1h_usd_per_mtok"]),
        "cache_read_usd_per_mtok": float(row["cache_read_usd_per_mtok"]),
        "output_usd_per_mtok": float(row["output_usd_per_mtok"]),
        "sort_order": int(row["sort_order"]),
        "source": row["source"],
        "updated_at": row["updated_at"],
    }


def list_pricing() -> list:
    with get_db() as con:
        rows = con.execute(
            """SELECT model_pattern, display_name, input_usd_per_mtok,
                      cache_write_5m_usd_per_mtok, cache_write_1h_usd_per_mtok,
                      cache_read_usd_per_mtok, output_usd_per_mtok, sort_order, source, updated_at
               FROM model_pricing ORDER BY sort_order, model_pattern"""
        ).fetchall()
    return [_to_item(r) for r in rows]


def save_pricing(items: list) -> int:
    """A teljes árlistát szinkronizálja (rács-szerkesztő): a payloadban nem szereplő
    mintákat törli, a többit beszúrja/frissíti. Forrás: 'manual'. Visszaadja a sorok számát."""
    if not items:
        raise ValueError("Üres árlista — legalább egy modell kötelező")
    ts = now_iso()
    patterns = [it["model_pattern"] for it in items]
    if len(set(patterns)) != len(patterns):
        raise ValueError("Ismétlődő model_pattern az árlistában")
    with get_db() as con:
        con.execute("DELETE FROM model_pricing WHERE NOT (model_pattern = ANY(%s))", (patterns,))
        for it in items:
            con.execute(
                """INSERT INTO model_pricing
                   (model_pattern, display_name, input_usd_per_mtok, cache_write_5m_usd_per_mtok,
                    cache_write_1h_usd_per_mtok, cache_read_usd_per_mtok, output_usd_per_mtok,
                    sort_order, source, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (model_pattern) DO UPDATE SET
                     display_name = EXCLUDED.display_name,
                     input_usd_per_mtok = EXCLUDED.input_usd_per_mtok,
                     cache_write_5m_usd_per_mtok = EXCLUDED.cache_write_5m_usd_per_mtok,
                     cache_write_1h_usd_per_mtok = EXCLUDED.cache_write_1h_usd_per_mtok,
                     cache_read_usd_per_mtok = EXCLUDED.cache_read_usd_per_mtok,
                     output_usd_per_mtok = EXCLUDED.output_usd_per_mtok,
                     sort_order = EXCLUDED.sort_order,
                     source = EXCLUDED.source,
                     updated_at = EXCLUDED.updated_at""",
                (it["model_pattern"], it["display_name"], it["input_usd_per_mtok"],
                 it["cache_write_5m_usd_per_mtok"], it["cache_write_1h_usd_per_mtok"],
                 it["cache_read_usd_per_mtok"], it["output_usd_per_mtok"],
                 it.get("sort_order", 100), it.get("source", "manual"), ts),
            )
    return len(items)


# ================================================================
# Hivatalos árlista letöltése + parse
# ================================================================

class _TableExtractor(HTMLParser):
    """Minimál HTML-tábla kinyerő: tables = list[ list[row] ], row = list[cell-szöveg]."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        # Defenzív: az html.parser nem zárja le automatikusan a tageket, így rosszul
        # beágyazott markup esetén self._row/_table már None lehet — ne szálljunk el.
        if tag in ("td", "th"):
            if self._cell is not None and self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._row is not None and self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag == "table":
            if self._table is not None:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _display_to_pattern(name: str) -> str:
    """'Claude Opus 4.8 (deprecated)' → 'claude-opus-4-8' (előtag-illesztéshez)."""
    name = re.sub(r"\(.*?\)", "", name)  # zárójeles megjegyzések ('(deprecated)') eldobása
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    return "-".join(tokens)


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", name)).strip()


def _parse_price(text: str):
    """'$12.50 / MTok' → 12.5; ha nem ár, None."""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", (text or "").replace(",", ""))
    return float(m.group(1)) if m else None


def _resolve_columns(header):
    """Fejléc-sor → {ár-kulcs: oszlopindex}. Ha bármely kötelező oszlop hiányzik → None."""
    low = [(c or "").lower() for c in header]
    cols = {}
    for key, needles in _HEADER_PATTERNS:
        idx = next((i for i, h in enumerate(low) if any(n in h for n in needles)), None)
        if idx is None:
            return None
        cols[key] = idx
    return cols


def _select_model_table(tables):
    """A modell-árazás táblát választja: fejléce mind az 5 ár-oszlopot tartalmazza, és van
    benne 'claude' adatsor. Több jelölt közül a legtöbb Claude-sort tartalmazót preferálja."""
    best, best_cols, best_count = None, None, 0
    for t in tables:
        if len(t) < 2:
            continue
        cols = _resolve_columns(t[0])
        if not cols:
            continue
        claude_rows = sum(1 for r in t[1:] if r and "claude" in (r[0] or "").lower())
        if claude_rows > best_count:
            best, best_cols, best_count = t, cols, claude_rows
    return (best, best_cols) if best else (None, None)


def parse_pricing_html(html: str) -> list:
    """A docs oldal HTML-jéből a modell-árazás táblát parse-olja (oszlopok fejléccímke
    alapján). Hibás/értelmezhetetlen formátum → [] (a hívó RuntimeError-t ad)."""
    ext = _TableExtractor()
    try:
        ext.feed(html)
    except Exception:
        return []
    table, cols = _select_model_table(ext.tables)
    if not table:
        return []
    out = []
    for row in table[1:]:
        name = row[0] if row else ""
        if "claude" not in name.lower():
            continue
        if any(idx >= len(row) for idx in cols.values()):
            continue
        prices = {key: _parse_price(row[idx]) for key, idx in cols.items()}
        if any(v is None for v in prices.values()):
            continue
        pat = _display_to_pattern(name)
        if not pat:
            continue
        item = {"model_pattern": pat, "display_name": _clean_name(name)}
        item.update(prices)
        out.append(item)
    return out


def fetch_official_pricing() -> list:
    """Letölti és parse-olja a hivatalos árlistát. Hibára RuntimeError (NO FALLBACK — nem ad csendben üreset)."""
    try:
        resp = httpx.get(
            PRICING_DOC_URL,
            headers={"User-Agent": "Mozilla/5.0 (AnthropicManager pricing refresh)"},
            timeout=30.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"A hivatalos árlista letöltése sikertelen: {e}") from e
    try:
        items = parse_pricing_html(resp.text)
    except Exception as e:
        raise RuntimeError(f"A hivatalos árlista értelmezése sikertelen: {e}") from e
    if not items:
        raise RuntimeError(
            "Nem sikerült értelmezni a hivatalos árlista táblát (a docs oldal formátuma változhatott). "
            "Frissítsd kézzel az árakat."
        )
    return items
