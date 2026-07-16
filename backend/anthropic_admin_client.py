"""
Anthropic Admin API kliens (httpx, szinkron).

Lefedi a használat/költség riportokat, a Claude Code analitikát és az admin
metaadatokat (workspaces, api_keys, users, organization). Kétféle lapozást kezel:
  - usage/cost/claude_code: `has_more` + `next_page` → `page`
  - workspaces/api_keys/users: `has_more` + `last_id` → `after_id`

Hibakezelés: 429 és 5xx esetén exponenciális backoff (Retry-After fejlécet
tiszteletben tartva), egyéb 4xx esetén AdminApiError (nincs csendes elnyelés).
"""
import time
from typing import Iterator, Optional

import httpx

from config import ANTHROPIC_API_BASE, ANTHROPIC_VERSION, USER_AGENT


MAX_RETRIES = 5
BASE_DELAY = 2.0
MAX_DELAY = 60.0
REQUEST_TIMEOUT = 60.0


class AdminApiError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Admin API hiba ({status_code}): {message}")


class AnthropicAdminClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Admin API kulcs kötelező")
        self._client = httpx.Client(
            base_url=ANTHROPIC_API_BASE,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "User-Agent": USER_AGENT,
            },
            timeout=REQUEST_TIMEOUT,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._client.close()

    # ----------------------------------------------------------------
    # Alacsony szintű GET backoff-fal
    # ----------------------------------------------------------------

    def _get(self, path: str, params) -> dict:
        last_err: Optional[AdminApiError] = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.get(path, params=params)
            except httpx.RequestError as e:
                last_err = AdminApiError(0, f"Hálózati hiba: {e}")
                time.sleep(min(BASE_DELAY * (2 ** attempt), MAX_DELAY))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else BASE_DELAY * (2 ** attempt)
                except ValueError:
                    delay = BASE_DELAY * (2 ** attempt)
                last_err = AdminApiError(resp.status_code, _extract_error(resp))
                if attempt < MAX_RETRIES - 1:
                    time.sleep(min(delay, MAX_DELAY))
                    continue
                raise last_err

            if resp.status_code >= 400:
                raise AdminApiError(resp.status_code, _extract_error(resp))

            return resp.json()

        raise last_err or AdminApiError(0, "Ismeretlen hiba")

    # ----------------------------------------------------------------
    # Szervezet
    # ----------------------------------------------------------------

    def get_org(self) -> dict:
        return self._get("/v1/organizations/me", None)

    # ----------------------------------------------------------------
    # Usage riport — group_by[]/szűrő tömbök, page-token lapozás
    # ----------------------------------------------------------------

    def iter_usage(self, starting_at: str, ending_at: str, group_by: list[str],
                   bucket_width: str = "1d", limit: int = 31,
                   extra_filters: Optional[list[tuple]] = None) -> Iterator[dict]:
        base = [
            ("starting_at", starting_at),
            ("ending_at", ending_at),
            ("bucket_width", bucket_width),
            ("limit", str(limit)),
        ]
        for gb in group_by:
            base.append(("group_by[]", gb))
        if extra_filters:
            base.extend(extra_filters)
        page = None
        while True:
            params = list(base)
            if page:
                params.append(("page", page))
            resp = self._get("/v1/organizations/usage_report/messages", params)
            for bucket in resp.get("data", []):
                for result in bucket.get("results", []):
                    yield {
                        "bucket_start": bucket["starting_at"],
                        "bucket_end": bucket["ending_at"],
                        "bucket_width": bucket_width,
                        "result": result,
                    }
            if resp.get("has_more") and resp.get("next_page"):
                page = resp["next_page"]
            else:
                break

    # ----------------------------------------------------------------
    # Cost riport — bucket_width mindig 1d
    # ----------------------------------------------------------------

    def iter_cost(self, starting_at: str, ending_at: str,
                  group_by: list[str], limit: int = 31) -> Iterator[dict]:
        base = [
            ("starting_at", starting_at),
            ("ending_at", ending_at),
            ("bucket_width", "1d"),
            ("limit", str(limit)),
        ]
        for gb in group_by:
            base.append(("group_by[]", gb))
        page = None
        while True:
            params = list(base)
            if page:
                params.append(("page", page))
            resp = self._get("/v1/organizations/cost_report", params)
            for bucket in resp.get("data", []):
                for result in bucket.get("results", []):
                    yield {
                        "bucket_start": bucket["starting_at"],
                        "bucket_end": bucket["ending_at"],
                        "result": result,
                    }
            if resp.get("has_more") and resp.get("next_page"):
                page = resp["next_page"]
            else:
                break

    # ----------------------------------------------------------------
    # Claude Code analitika — egyetlen nap, page-token lapozás
    # ----------------------------------------------------------------

    def iter_claude_code(self, day: str, limit: int = 1000) -> Iterator[dict]:
        base = [("starting_at", day), ("limit", str(limit))]
        page = None
        while True:
            params = list(base)
            if page:
                params.append(("page", page))
            resp = self._get("/v1/organizations/usage_report/claude_code", params)
            for record in resp.get("data", []):
                yield record
            if resp.get("has_more") and resp.get("next_page"):
                page = resp["next_page"]
            else:
                break

    # ----------------------------------------------------------------
    # Metaadatok — cursor (after_id) lapozás
    # ----------------------------------------------------------------

    def _iter_cursor(self, path: str, extra: Optional[list[tuple]] = None) -> Iterator[dict]:
        after_id = None
        while True:
            params = [("limit", "1000")]
            if extra:
                params.extend(extra)
            if after_id:
                params.append(("after_id", after_id))
            resp = self._get(path, params)
            data = resp.get("data", [])
            for item in data:
                yield item
            if resp.get("has_more"):
                if not resp.get("last_id"):
                    raise RuntimeError(
                        f"Admin API lapozási hiba: has_more=true, de last_id hiányzik ({path})"
                    )
                after_id = resp["last_id"]
            else:
                break

    def iter_workspaces(self, include_archived: bool = True) -> Iterator[dict]:
        extra = [("include_archived", "true")] if include_archived else None
        return self._iter_cursor("/v1/organizations/workspaces", extra)

    def iter_api_keys(self) -> Iterator[dict]:
        return self._iter_cursor("/v1/organizations/api_keys")

    def iter_members(self) -> Iterator[dict]:
        return self._iter_cursor("/v1/organizations/users")


def _extract_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") or {}
        return err.get("message") or body.get("message") or resp.text[:300]
    except Exception:
        return (resp.text or "")[:300]


def test_connection(api_key: str) -> dict:
    """Kulcs tesztelése: /me lekérés. Visszaad: organization dict. Hibára AdminApiError."""
    with AnthropicAdminClient(api_key) as client:
        return client.get_org()
