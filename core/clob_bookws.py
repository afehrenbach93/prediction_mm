"""
Live mid from CLOB market websocket, with REST fallback.

  wss://ws-subscriptions-clob.polymarket.com/ws/market

Maintains best bid/ask/mid in memory; runner falls back to REST /book on drop.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class BookMidCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.mids: dict[str, float] = {}
        self.best: dict[str, tuple[float | None, float | None]] = {}
        self.last_update: dict[str, float] = {}
        self.connected = False
        self.last_error = ""

    def set_mid(self, token_id: str, mid: float, bid: float | None = None,
                ask: float | None = None):
        with self._lock:
            self.mids[str(token_id)] = mid
            if bid is not None or ask is not None:
                prev = self.best.get(str(token_id), (None, None))
                self.best[str(token_id)] = (
                    bid if bid is not None else prev[0],
                    ask if ask is not None else prev[1],
                )
            self.last_update[str(token_id)] = time.time()

    def get_mid(self, token_id: str, max_age: float = 5.0) -> float | None:
        with self._lock:
            ts = self.last_update.get(str(token_id))
            if ts is None or (time.time() - ts) > max_age:
                return None
            return self.mids.get(str(token_id))


class MarketWsThread:
    """Background WS subscriber. No-op if websocket-client not installed."""

    def __init__(self, asset_ids: list[str], cache: BookMidCache,
                 on_mid: Callable[[str, float], None] | None = None):
        self.asset_ids = [str(a) for a in asset_ids]
        self.cache = cache
        self.on_mid = on_mid
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if not self.asset_ids:
            return
        self._thread = threading.Thread(target=self._run, name="clob-book-ws", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import websocket  # websocket-client
        except ImportError:
            self.cache.last_error = "websocket-client not installed"
            return

        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._session(websocket)
                backoff = 1.0
            except Exception as e:
                self.cache.connected = False
                self.cache.last_error = str(e)
                time.sleep(backoff)
                backoff = min(60.0, backoff * 2)

    def _session(self, websocket):
        done = threading.Event()

        def on_open(ws):
            self.cache.connected = True
            ws.send(json.dumps({
                "assets_ids": self.asset_ids,
                "type": "market",
                "custom_feature_enabled": True,
            }))

        def on_message(ws, message):
            if message == "PONG":
                return
            try:
                data = json.loads(message)
            except Exception:
                return
            events = data if isinstance(data, list) else [data]
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                et = ev.get("event_type") or ev.get("type") or ""
                asset = str(ev.get("asset_id") or ev.get("asset") or "")
                if et in ("best_bid_ask", "price_change", "last_trade_price", "book"):
                    bid = ev.get("best_bid") or ev.get("bid")
                    ask = ev.get("best_ask") or ev.get("ask")
                    mid = ev.get("mid") or ev.get("midpoint")
                    try:
                        if mid is not None:
                            m = float(mid)
                        elif bid is not None and ask is not None:
                            m = (float(bid) + float(ask)) / 2.0
                        else:
                            continue
                        if not asset:
                            continue
                        self.cache.set_mid(
                            asset, m,
                            float(bid) if bid is not None else None,
                            float(ask) if ask is not None else None,
                        )
                        if self.on_mid:
                            self.on_mid(asset, m)
                    except (TypeError, ValueError):
                        continue

        def on_error(ws, err):
            self.cache.last_error = str(err)
            self.cache.connected = False

        def on_close(ws, *args):
            self.cache.connected = False
            done.set()

        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        def ping_loop():
            while not self._stop.is_set() and not done.is_set():
                try:
                    ws.send("PING")
                except Exception:
                    break
                time.sleep(10)

        t = threading.Thread(target=ping_loop, daemon=True)
        t.start()
        ws.run_forever(ping_interval=0)
        done.set()
