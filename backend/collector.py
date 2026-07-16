"""
Adatgyűjtő: az Admin API riportjait/metaadatait normalizálja és idempotensen
a helyi ténytáblákba upsert-eli.

Idempotencia: minden ténysorhoz determinisztikus `dim_hash` (md5 a dimenziók
kanonikus konkatenációjából), és UNIQUE (bucket, dim_hash). Az ON CONFLICT DO
UPDATE a frissen revideált bucket-eket felülírja — kétszeri futtatás nem duplikál.

A `run_sync` orchestrálja: advisory lock (átfedés-zár), sync_runs audit, aktív
Admin kulcs, dátum-ablak chunkolás (usage/cost ≤31 napos blokkok, claude_code
naponként), majd a futás lezárása.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from dateutil import parser as dtparser

import admin_key_service
from anthropic_admin_client import AnthropicAdminClient
from database import get_db, advisory_lock, now_iso, savepoint
from settings_service import get_setting


# ================================================================
# Segédfüggvények
# ================================================================

def _dim_hash(parts: list) -> str:
    canon = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.md5(canon.encode("utf-8")).hexdigest()


def _parse_ts(s: str) -> datetime:
    return dtparser.isoparse(s)


def _iso_midnight(d: date) -> str:
    return d.isoformat() + "T00:00:00Z"


def _to_date(s: str) -> date:
    return dtparser.isoparse(s).date()


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v is not None else Decimal(0)
    except Exception:
        return Decimal(0)


# ================================================================
# USAGE
# ================================================================

USAGE_GROUP_BY = ["model", "workspace_id", "api_key_id", "service_tier", "context_window"]


def collect_usage(client: AnthropicAdminClient, starting_at: str, ending_at: str) -> int:
    count = 0
    ts = now_iso()
    with get_db() as con:
        for item in client.iter_usage(starting_at, ending_at, USAGE_GROUP_BY, "1d", 31):
            r = item["result"]
            cc = r.get("cache_creation") or {}
            stu = r.get("server_tool_use") or {}
            dims = [
                item["bucket_width"],
                r.get("api_key_id"), r.get("workspace_id"), r.get("model"),
                r.get("service_tier"), r.get("context_window"), r.get("inference_geo"),
                r.get("account_id"), r.get("service_account_id"),
            ]
            dh = _dim_hash(dims)
            con.execute(
                """INSERT INTO usage_facts
                   (bucket_start, bucket_end, bucket_width, api_key_id, workspace_id, model,
                    service_tier, context_window, inference_geo, account_id, service_account_id,
                    uncached_input_tokens, cache_creation_1h_tokens, cache_creation_5m_tokens,
                    cache_read_input_tokens, output_tokens, web_search_requests,
                    dim_hash, collected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (bucket_start, bucket_width, dim_hash) DO UPDATE SET
                       bucket_end = EXCLUDED.bucket_end,
                       uncached_input_tokens = EXCLUDED.uncached_input_tokens,
                       cache_creation_1h_tokens = EXCLUDED.cache_creation_1h_tokens,
                       cache_creation_5m_tokens = EXCLUDED.cache_creation_5m_tokens,
                       cache_read_input_tokens = EXCLUDED.cache_read_input_tokens,
                       output_tokens = EXCLUDED.output_tokens,
                       web_search_requests = EXCLUDED.web_search_requests,
                       collected_at = EXCLUDED.collected_at""",
                (
                    _parse_ts(item["bucket_start"]), _parse_ts(item["bucket_end"]),
                    item["bucket_width"], r.get("api_key_id"), r.get("workspace_id"),
                    r.get("model"), r.get("service_tier"), r.get("context_window"),
                    r.get("inference_geo"), r.get("account_id"), r.get("service_account_id"),
                    int(r.get("uncached_input_tokens") or 0),
                    int(cc.get("ephemeral_1h_input_tokens") or 0),
                    int(cc.get("ephemeral_5m_input_tokens") or 0),
                    int(r.get("cache_read_input_tokens") or 0),
                    int(r.get("output_tokens") or 0),
                    int(stu.get("web_search_requests") or 0),
                    dh, ts,
                ),
            )
            count += 1
    return count


def collect_usage_today(client: AnthropicAdminClient, day: date) -> int:
    """A mai (még nyitott) napot 1h bontásban kéri le és napi szintre aggregálja,
    majd dim-enként EGYETLEN 1d sorként upsert-eli.

    Indok: az Usage API a folyamatban lévő mai napot 1d granularitásban nem adja
    vissza, csak 1h-ban. Így a mai részérték is látszik; másnap a valódi 1d bucket
    ugyanezzel a (nap, '1d', dim_hash) kulccsal felülírja. Nincs dupla számolás,
    mert nem tárolunk órás sorokat — memóriában összegzünk.
    """
    start = _iso_midnight(day)
    end = _iso_midnight(day + timedelta(days=1))
    agg: dict = {}        # dim-enként a teljes napra (→ 1d sor)
    hourly: dict = {}     # (óra, modell)-enként (→ usage_hourly_facts, az egyenleg pontos-idő számításához)
    for item in client.iter_usage(start, end, USAGE_GROUP_BY, "1h", 168):
        r = item["result"]
        cc = r.get("cache_creation") or {}
        stu = r.get("server_tool_use") or {}
        vals = (
            int(r.get("uncached_input_tokens") or 0),
            int(cc.get("ephemeral_1h_input_tokens") or 0),
            int(cc.get("ephemeral_5m_input_tokens") or 0),
            int(r.get("cache_read_input_tokens") or 0),
            int(r.get("output_tokens") or 0),
            int(stu.get("web_search_requests") or 0),
        )
        key = (
            r.get("api_key_id"), r.get("workspace_id"), r.get("model"),
            r.get("service_tier"), r.get("context_window"), r.get("inference_geo"),
            r.get("account_id"), r.get("service_account_id"),
        )
        a = agg.setdefault(key, [0, 0, 0, 0, 0, 0])
        h = hourly.setdefault((item["bucket_start"], r.get("model")), [0, 0, 0, 0, 0, 0])
        for i in range(6):
            a[i] += vals[i]
            h[i] += vals[i]

    if not agg:
        return 0

    ts = now_iso()
    bs, be = _parse_ts(start), _parse_ts(end)
    count = 0
    with get_db() as con:
        # Órás sorok (modellenként) — az egyenleg horgony-napi, pontos-idő részéhez.
        for (hour_iso, model), h in hourly.items():
            con.execute(
                """INSERT INTO usage_hourly_facts
                   (bucket_start, model, uncached_input_tokens, cache_creation_1h_tokens,
                    cache_creation_5m_tokens, cache_read_input_tokens, output_tokens,
                    web_search_requests, dim_hash, collected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (bucket_start, dim_hash) DO UPDATE SET
                       uncached_input_tokens = EXCLUDED.uncached_input_tokens,
                       cache_creation_1h_tokens = EXCLUDED.cache_creation_1h_tokens,
                       cache_creation_5m_tokens = EXCLUDED.cache_creation_5m_tokens,
                       cache_read_input_tokens = EXCLUDED.cache_read_input_tokens,
                       output_tokens = EXCLUDED.output_tokens,
                       web_search_requests = EXCLUDED.web_search_requests,
                       collected_at = EXCLUDED.collected_at""",
                (_parse_ts(hour_iso), model, h[0], h[1], h[2], h[3], h[4], h[5],
                 _dim_hash([model]), ts),
            )
        # Régi órás sorok takarítása (csak a friss horgonyhoz kell néhány nap).
        con.execute("DELETE FROM usage_hourly_facts WHERE bucket_start < %s",
                    (_iso_midnight(day - timedelta(days=3)),))

        for key, a in agg.items():
            dims = ["1d", *key]
            dh = _dim_hash(dims)
            con.execute(
                """INSERT INTO usage_facts
                   (bucket_start, bucket_end, bucket_width, api_key_id, workspace_id, model,
                    service_tier, context_window, inference_geo, account_id, service_account_id,
                    uncached_input_tokens, cache_creation_1h_tokens, cache_creation_5m_tokens,
                    cache_read_input_tokens, output_tokens, web_search_requests,
                    dim_hash, collected_at)
                   VALUES (%s,%s,'1d',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (bucket_start, bucket_width, dim_hash) DO UPDATE SET
                       bucket_end = EXCLUDED.bucket_end,
                       uncached_input_tokens = EXCLUDED.uncached_input_tokens,
                       cache_creation_1h_tokens = EXCLUDED.cache_creation_1h_tokens,
                       cache_creation_5m_tokens = EXCLUDED.cache_creation_5m_tokens,
                       cache_read_input_tokens = EXCLUDED.cache_read_input_tokens,
                       output_tokens = EXCLUDED.output_tokens,
                       web_search_requests = EXCLUDED.web_search_requests,
                       collected_at = EXCLUDED.collected_at""",
                (bs, be, key[0], key[1], key[2], key[3], key[4], key[5], key[6], key[7],
                 a[0], a[1], a[2], a[3], a[4], a[5], dh, ts),
            )
            count += 1
    return count


# ================================================================
# COST
# ================================================================

COST_GROUP_BY = ["workspace_id", "description"]


def collect_cost(client: AnthropicAdminClient, starting_at: str, ending_at: str) -> int:
    count = 0
    ts = now_iso()
    with get_db() as con:
        for item in client.iter_cost(starting_at, ending_at, COST_GROUP_BY, 31):
            r = item["result"]
            dims = [
                "1d", r.get("workspace_id"), r.get("description"), r.get("cost_type"),
                r.get("model"), r.get("token_type"), r.get("context_window"),
                r.get("service_tier"), r.get("inference_geo"),
            ]
            dh = _dim_hash(dims)
            con.execute(
                """INSERT INTO cost_facts
                   (bucket_start, bucket_end, bucket_width, workspace_id, description, cost_type,
                    model, token_type, context_window, service_tier, inference_geo,
                    currency, amount_cents, dim_hash, collected_at)
                   VALUES (%s,%s,'1d',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (bucket_start, bucket_width, dim_hash) DO UPDATE SET
                       bucket_end = EXCLUDED.bucket_end,
                       currency = EXCLUDED.currency,
                       amount_cents = EXCLUDED.amount_cents,
                       collected_at = EXCLUDED.collected_at""",
                (
                    _parse_ts(item["bucket_start"]), _parse_ts(item["bucket_end"]),
                    r.get("workspace_id"), r.get("description"), r.get("cost_type"),
                    r.get("model"), r.get("token_type"), r.get("context_window"),
                    r.get("service_tier"), r.get("inference_geo"),
                    r.get("currency") or "USD", _dec(r.get("amount")), dh, ts,
                ),
            )
            count += 1
    return count


# ================================================================
# CLAUDE CODE
# ================================================================

def collect_claude_code(client: AnthropicAdminClient, day: str) -> int:
    count = 0
    ts = now_iso()
    with get_db() as con:
        for rec in client.iter_claude_code(day):
            actor = rec.get("actor") or {}
            core = rec.get("core_metrics") or {}
            loc = core.get("lines_of_code") or {}
            ta = rec.get("tool_actions") or {}
            models = rec.get("model_breakdown") or []

            def tool(name):
                t = ta.get(name) or {}
                return int(t.get("accepted") or 0), int(t.get("rejected") or 0)

            edit_a, edit_r = tool("edit_tool")
            medit_a, medit_r = tool("multi_edit_tool")
            write_a, write_r = tool("write_tool")
            nb_a, nb_r = tool("notebook_edit_tool")

            total_in = total_out = 0
            est_cost = Decimal(0)
            for m in models:
                tk = m.get("tokens") or {}
                total_in += int(tk.get("input") or 0)
                total_out += int(tk.get("output") or 0)
                ec = m.get("estimated_cost") or {}
                est_cost += _dec(ec.get("amount"))

            actor_email = actor.get("email_address")
            actor_api_key_name = actor.get("api_key_name")
            dims = [
                actor.get("type"), actor_email, actor_api_key_name,
                rec.get("customer_type"), rec.get("terminal_type"),
            ]
            dh = _dim_hash(dims)

            row = con.execute(
                """INSERT INTO claude_code_facts
                   (day, actor_type, actor_email, actor_api_key_name, customer_type, terminal_type,
                    num_sessions, lines_added, lines_removed, commits, pull_requests,
                    edit_accepted, edit_rejected, multi_edit_accepted, multi_edit_rejected,
                    write_accepted, write_rejected, notebook_edit_accepted, notebook_edit_rejected,
                    total_input_tokens, total_output_tokens, estimated_cost_cents,
                    dim_hash, collected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (day, dim_hash) DO UPDATE SET
                       actor_type = EXCLUDED.actor_type,
                       actor_email = EXCLUDED.actor_email,
                       actor_api_key_name = EXCLUDED.actor_api_key_name,
                       customer_type = EXCLUDED.customer_type,
                       terminal_type = EXCLUDED.terminal_type,
                       num_sessions = EXCLUDED.num_sessions,
                       lines_added = EXCLUDED.lines_added,
                       lines_removed = EXCLUDED.lines_removed,
                       commits = EXCLUDED.commits,
                       pull_requests = EXCLUDED.pull_requests,
                       edit_accepted = EXCLUDED.edit_accepted,
                       edit_rejected = EXCLUDED.edit_rejected,
                       multi_edit_accepted = EXCLUDED.multi_edit_accepted,
                       multi_edit_rejected = EXCLUDED.multi_edit_rejected,
                       write_accepted = EXCLUDED.write_accepted,
                       write_rejected = EXCLUDED.write_rejected,
                       notebook_edit_accepted = EXCLUDED.notebook_edit_accepted,
                       notebook_edit_rejected = EXCLUDED.notebook_edit_rejected,
                       total_input_tokens = EXCLUDED.total_input_tokens,
                       total_output_tokens = EXCLUDED.total_output_tokens,
                       estimated_cost_cents = EXCLUDED.estimated_cost_cents,
                       collected_at = EXCLUDED.collected_at
                   RETURNING id""",
                (
                    day, actor.get("type"), actor_email, actor_api_key_name,
                    rec.get("customer_type"), rec.get("terminal_type"),
                    int(core.get("num_sessions") or 0),
                    int(loc.get("added") or 0), int(loc.get("removed") or 0),
                    int(core.get("commits_by_claude_code") or 0),
                    int(core.get("pull_requests_by_claude_code") or 0),
                    edit_a, edit_r, medit_a, medit_r, write_a, write_r, nb_a, nb_r,
                    total_in, total_out, est_cost, dh, ts,
                ),
            ).fetchone()
            fact_id = row["id"]

            # A modell-bontás gyerek sorait újraírjuk (idempotens).
            con.execute("DELETE FROM claude_code_model_facts WHERE fact_id = %s", (fact_id,))
            for m in models:
                tk = m.get("tokens") or {}
                ec = m.get("estimated_cost") or {}
                con.execute(
                    """INSERT INTO claude_code_model_facts
                       (fact_id, model, input_tokens, output_tokens, cache_read_tokens,
                        cache_creation_tokens, estimated_cost_cents)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        fact_id, m.get("model"),
                        int(tk.get("input") or 0), int(tk.get("output") or 0),
                        int(tk.get("cache_read") or 0), int(tk.get("cache_creation") or 0),
                        _dec(ec.get("amount")),
                    ),
                )
            count += 1
    return count


# ================================================================
# METAADATOK (workspaces / api_keys / members)
# ================================================================

def collect_metadata(client: AnthropicAdminClient) -> tuple[int, list[str]]:
    """Visszaad: (sikeresen upsert-elt sorok száma, hibaüzenetek listája).

    Minden sor saját SAVEPOINT-ban fut: egy hibás rekord (pl. érvénytelen
    mező) csak azt az egy sort görgeti vissza, a többi feldolgozása folytatódik.
    """
    count = 0
    errors: list[str] = []
    ts = now_iso()
    with get_db() as con:
        for ws in client.iter_workspaces(include_archived=True):
            try:
                with savepoint(con):
                    con.execute(
                        """INSERT INTO workspaces (id, name, display_color, archived_at, created_at, raw, synced_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (id) DO UPDATE SET
                               name = EXCLUDED.name, display_color = EXCLUDED.display_color,
                               archived_at = EXCLUDED.archived_at, created_at = EXCLUDED.created_at,
                               raw = EXCLUDED.raw, synced_at = EXCLUDED.synced_at""",
                        (ws.get("id"), ws.get("name"), ws.get("display_color"),
                         ws.get("archived_at"), ws.get("created_at"), json.dumps(ws), ts),
                    )
                count += 1
            except Exception as e:
                errors.append(f"workspace {ws.get('id')}: {e}")

        for k in client.iter_api_keys():
            try:
                with savepoint(con):
                    con.execute(
                        """INSERT INTO org_api_keys (id, name, workspace_id, status, partial_key_hint, created_at, raw, synced_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (id) DO UPDATE SET
                               name = EXCLUDED.name, workspace_id = EXCLUDED.workspace_id,
                               status = EXCLUDED.status, partial_key_hint = EXCLUDED.partial_key_hint,
                               created_at = EXCLUDED.created_at, raw = EXCLUDED.raw, synced_at = EXCLUDED.synced_at""",
                        (k.get("id"), k.get("name"), k.get("workspace_id"), k.get("status"),
                         k.get("partial_key_hint"), k.get("created_at"), json.dumps(k), ts),
                    )
                count += 1
            except Exception as e:
                errors.append(f"api_key {k.get('id')}: {e}")

        for m in client.iter_members():
            try:
                with savepoint(con):
                    con.execute(
                        """INSERT INTO org_members (id, email, name, role, raw, synced_at)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (id) DO UPDATE SET
                               email = EXCLUDED.email, name = EXCLUDED.name, role = EXCLUDED.role,
                               raw = EXCLUDED.raw, synced_at = EXCLUDED.synced_at""",
                        (m.get("id"), m.get("email"), m.get("name"), m.get("role"), json.dumps(m), ts),
                    )
                count += 1
            except Exception as e:
                errors.append(f"member {m.get('id')}: {e}")
    return count, errors


# ================================================================
# Dátum-ablak chunkolás
# ================================================================

def _sync_usage_range(client, starting_at: str, ending_at: str) -> int:
    s, e = _to_date(starting_at), _to_date(ending_at)
    total, cur = 0, s
    while cur < e:
        chunk_end = min(cur + timedelta(days=31), e)
        total += collect_usage(client, _iso_midnight(cur), _iso_midnight(chunk_end))
        cur = chunk_end
    return total


def _sync_cost_range(client, starting_at: str, ending_at: str) -> int:
    s, e = _to_date(starting_at), _to_date(ending_at)
    total, cur = 0, s
    while cur < e:
        chunk_end = min(cur + timedelta(days=31), e)
        total += collect_cost(client, _iso_midnight(cur), _iso_midnight(chunk_end))
        cur = chunk_end
    return total


def _sync_claude_code_range(client, starting_at: str, ending_at: str) -> int:
    s, e = _to_date(starting_at), _to_date(ending_at)
    total, d = 0, s
    while d < e:
        total += collect_claude_code(client, d.isoformat())
        d += timedelta(days=1)
    return total


def _default_window():
    """Gördülő ablak a settings alapján: (starting_at, ending_at) RFC3339 éjfélkor.

    ending_at = holnap 00:00Z (hogy a mai bucket benne legyen),
    starting_at = (ma - rolling_window_days) 00:00Z.
    """
    rolling = int(get_setting("scheduler.rolling_window_days"))
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=rolling)
    end = today + timedelta(days=1)
    return _iso_midnight(start), _iso_midnight(end)


# ================================================================
# run_sync — orchestráció (advisory lock + sync_runs audit)
# ================================================================

def _start_run(source: str, trigger: str, params: dict) -> int:
    with get_db() as con:
        row = con.execute(
            """INSERT INTO sync_runs (source, trigger, status, started_at, params)
               VALUES (%s,%s,'running',%s,%s) RETURNING id""",
            (source, trigger, now_iso(), json.dumps(params)),
        ).fetchone()
    return row["id"]


def _finish_run(run_id: int, status: str, rows: int = 0, error: str = None) -> None:
    with get_db() as con:
        con.execute(
            """UPDATE sync_runs SET status=%s, finished_at=%s, rows_upserted=%s, error=%s
               WHERE id=%s""",
            (status, now_iso(), rows, error, run_id),
        )


def run_sync(source: str, trigger: str = "manual",
             starting_at: str = None, ending_at: str = None) -> dict:
    """Egy forrás szinkronizálása. Advisory lock véd az átfedés ellen.

    Visszaad: {"ok": bool, "rows": int, "run_id": int} vagy {"skipped": True, ...}.
    Hibát továbbdob (a hívó kezeli), de a sync_runs sort 'error'-ra zárja.
    """
    if source not in ("usage", "cost", "claude_code", "metadata"):
        raise ValueError(f"Ismeretlen forrás: {source}")

    with advisory_lock(source) as got_lock:
        if not got_lock:
            return {"skipped": True, "reason": "already_running", "source": source}

        if source != "metadata" and (not starting_at or not ending_at):
            starting_at, ending_at = _default_window()

        params = {"starting_at": starting_at, "ending_at": ending_at}
        run_id = _start_run(source, trigger, params)
        try:
            key = admin_key_service.get_active_key()
            with AnthropicAdminClient(key["value"]) as client:
                if source == "usage":
                    total = _sync_usage_range(client, starting_at, ending_at)
                    # A mai (nyitott) nap 1d bucketje nincs az API-ban; órásból pótoljuk.
                    today = datetime.now(timezone.utc).date()
                    if _to_date(starting_at) <= today < _to_date(ending_at):
                        total += collect_usage_today(client, today)
                elif source == "cost":
                    total = _sync_cost_range(client, starting_at, ending_at)
                elif source == "claude_code":
                    total = _sync_claude_code_range(client, starting_at, ending_at)
                else:
                    total, meta_errors = collect_metadata(client)
                    if meta_errors:
                        preview = "; ".join(meta_errors[:5])
                        summary = f"{len(meta_errors)} sor kihagyva: {preview}"
                        if total == 0:
                            raise RuntimeError(summary)
                        # Részleges siker: a jó sorok bekerültek, a hibásak logolva —
                        # nem görgetjük vissza az egészet egy rossz rekord miatt.
                        _finish_run(run_id, "partial", total, summary)
                        return {"ok": True, "rows": total, "run_id": run_id, "source": source,
                                "status": "partial", "error": summary}
            _finish_run(run_id, "ok", total)
            return {"ok": True, "rows": total, "run_id": run_id, "source": source}
        except Exception as e:
            _finish_run(run_id, "error", 0, str(e))
            raise
