"""
Minimal Supabase REST client for CLOB ledger/control (stdlib urllib).

Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY  (preferred for inserts) or SUPABASE_ANON_KEY
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class SupabaseClob:
    def __init__(self, url: str = "", key: str = ""):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = (
            key
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            or os.getenv("SUPABASE_ANON_KEY", "")
        )
        self.enabled = bool(self.url and self.key)

    def _headers(self) -> dict:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _req(self, method: str, path: str, body: Any = None,
             params: dict | None = None, prefer: str = "") -> tuple[int, Any]:
        if not self.enabled:
            return 0, {"_err": "supabase not configured"}
        q = ("?" + urllib.parse.urlencode(params or {}, doseq=True)) if params else ""
        url = f"{self.url}/rest/v1/{path}{q}"
        headers = self._headers()
        if prefer:
            headers["Prefer"] = prefer
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {"_raw": "(non-json)"}
        except Exception as e:
            return None, {"_err": str(e)}

    def insert(self, table: str, row: dict) -> tuple[int, Any]:
        return self._req("POST", table, row, prefer="return=minimal")

    def upsert(self, table: str, row: dict, on_conflict: str) -> tuple[int, Any]:
        return self._req(
            "POST", table, row,
            prefer=f"resolution=merge-duplicates,return=minimal",
            params={"on_conflict": on_conflict},
        )

    def select(self, table: str, params: dict | None = None) -> tuple[int, Any]:
        return self._req("GET", table, params=params or {})

    def get_kill(self) -> bool | None:
        """Return True/False from clob_control, or None if unavailable."""
        st, data = self.select("clob_control", {"id": "eq.1", "select": "kill"})
        if st != 200 or not isinstance(data, list) or not data:
            return None
        return bool(data[0].get("kill"))

    def set_kill(self, kill: bool, note: str = "") -> tuple[int, Any]:
        from datetime import datetime, timezone
        return self._req(
            "PATCH", "clob_control",
            {
                "kill": kill,
                "note": note,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            params={"id": "eq.1"},
            prefer="return=minimal",
        )

    def fetch_daily_pnl(self, limit: int = 60) -> list[dict]:
        st, data = self.select(
            "clob_daily_pnl",
            {"select": "*", "order": "day.desc", "limit": str(limit)},
        )
        if st != 200 or not isinstance(data, list):
            return []
        return list(reversed(data))
