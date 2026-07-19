"""
Read-only Polymarket global CLOB client (no auth, no orders).

Hosts:
  https://clob.polymarket.com  — sampling-markets, books

This is a different venue from Polymarket US (api.polymarket.us). Edge search
pivots here: liquidity rewards use quadratic scoring within max_spread.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

CLOB = "https://clob.polymarket.com"
UA = "prediction-mm/clob-scan"


class ClobClient:
    def __init__(self, base: str = CLOB, timeout: float = 45.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        url = self.base + path + q
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {"_raw": "(non-json error body)"}
        except Exception as e:
            return None, {"_err": str(e)}

    def get_sampling_markets_page(self, next_cursor: str | None = None):
        params = {}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return self._get("/sampling-markets", **params)

    def iter_sampling_markets(self, max_pages: int = 40):
        """Yield market dicts across all pages. Stops on empty page or LTE= cursor."""
        cursor = None
        for _ in range(max_pages):
            s, d = self.get_sampling_markets_page(cursor)
            if s != 200 or not isinstance(d, dict):
                break
            page = d.get("data") or []
            if not page:
                break
            for m in page:
                yield m
            nxt = d.get("next_cursor")
            # LTE= is base64("-1") — end sentinel; also stop on empty/missing
            if not nxt or nxt in ("LTE=", "-1"):
                break
            cursor = nxt

    def get_book(self, token_id: str):
        """Return (bids, asks) as lists of (price float, size float), best-first.
        CLOB returns bids ascending and asks descending in some snapshots — we
        normalize to best-first."""
        s, d = self._get("/book", token_id=token_id)
        if s != 200 or not isinstance(d, dict):
            return [], []

        def parse(levels, reverse: bool):
            out = []
            for e in levels or []:
                try:
                    out.append((float(e["price"]), float(e["size"])))
                except Exception:
                    pass
            out.sort(key=lambda x: x[0], reverse=reverse)
            return out

        # bids: highest first; asks: lowest first
        return parse(d.get("bids"), reverse=True), parse(d.get("asks"), reverse=False)
