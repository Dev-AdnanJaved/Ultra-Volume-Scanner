"""
Real-time WebSocket price monitor for open trades.

Replaces the 300-second polling gap with instant price updates.
As soon as a price crosses a TP level, SL, or emergency threshold,
it calls strategy_manager.realtime_price_update() immediately.

Architecture:
  - Subscribes to <symbol>@markPrice@1s for every open trade symbol
  - Reconnects automatically on disconnect
  - Dynamically updates subscriptions as trades open/close
  - Thread-safe: uses its own lock + SM's internal lock

Usage:
  monitor = WSPriceMonitor(strategy_mgr, config)
  monitor.start()
  ...
  monitor.update_symbols({"BTCUSDT", "ETHUSDT"})   # called when trades change
  monitor.stop()
"""

import json
import logging
import threading
import time
from typing import Optional, Set

logger = logging.getLogger(__name__)

# ── optional import (graceful fallback if not installed) ──────────────
try:
    import websocket  # websocket-client library
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning(
        "websocket-client not installed — real-time WS monitor disabled. "
        "Run: pip install websocket-client --break-system-packages"
    )


class WSPriceMonitor:
    """
    Subscribes to Binance USDT-M futures mark-price WebSocket streams.
    On each price tick, calls strategy_manager.realtime_price_update(symbol, price).
    """

    # Binance combined stream endpoints
    WS_MAINNET  = "wss://fstream.binance.com/stream"
    WS_TESTNET  = "wss://demo-fstream.binance.com/stream"   # Binance Futures Demo/Testnet

    RECONNECT_DELAY = 5    # seconds before reconnect attempt
    PING_INTERVAL   = 20   # WebSocket keepalive ping
    MAX_STREAMS     = 200  # Binance per-connection limit

    def __init__(self, strategy_manager, config: dict) -> None:
        self._sm       = strategy_manager
        self._testnet  = config.get("binance", {}).get("testnet", False)
        self._base_url = self.WS_TESTNET if self._testnet else self.WS_MAINNET

        self._lock       = threading.Lock()
        self._subscribed: Set[str] = set()
        self._running    = False
        self._ws         = None
        self._ws_thread  = None

    # ── public API ───────────────────────────────────────────────────

    def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.warning("WSPriceMonitor: websocket-client missing — not starting")
            return
        self._running = True
        logger.info("WSPriceMonitor: starting")
        self._launch_thread()

    def stop(self) -> None:
        self._running = False
        self._close_ws()
        logger.info("WSPriceMonitor: stopped")

    def update_symbols(self, symbols: Set[str]) -> None:
        """
        Call this whenever open trades change (new trade opened / trade closed).
        Reconnects the WebSocket with the updated subscription list.
        """
        if not _WS_AVAILABLE or not self._running:
            return
        with self._lock:
            new_syms = set(symbols) & set()  # will be filtered below
            new_syms = {s.upper() for s in symbols}
            if new_syms == self._subscribed:
                return
            self._subscribed = new_syms
            logger.info(
                "WSPriceMonitor: subscription updated → %d symbols", len(new_syms)
            )
        # Reconnect with new symbol list
        self._close_ws()

    # ── internals ────────────────────────────────────────────────────

    def _launch_thread(self) -> None:
        self._ws_thread = threading.Thread(
            target=self._run_loop, name="ws_monitor", daemon=True
        )
        self._ws_thread.start()

    def _run_loop(self) -> None:
        """Main loop: connect → receive → reconnect on disconnect."""
        while self._running:
            with self._lock:
                syms = set(self._subscribed)

            if not syms:
                time.sleep(2)
                continue

            url = self._build_url(syms)
            logger.info("WSPriceMonitor: connecting — %d symbols", len(syms))

            try:
                ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                with self._lock:
                    self._ws = ws
                ws.run_forever(
                    ping_interval=self.PING_INTERVAL,
                    ping_timeout=10,
                    reconnect=0,   # we handle reconnect ourselves
                )
            except Exception as exc:
                logger.error("WSPriceMonitor: connection error — %s", exc)

            if self._running:
                logger.info(
                    "WSPriceMonitor: reconnecting in %ds …", self.RECONNECT_DELAY
                )
                time.sleep(self.RECONNECT_DELAY)

    def _build_url(self, symbols: Set[str]) -> str:
        streams = "/".join(
            f"{s.lower()}@markPrice@1s"
            for s in sorted(symbols)[: self.MAX_STREAMS]
        )
        return f"{self._base_url}?streams={streams}"

    def _close_ws(self) -> None:
        with self._lock:
            ws = self._ws
            self._ws = None
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    # ── WebSocket callbacks ───────────────────────────────────────────

    def _on_open(self, ws) -> None:
        with self._lock:
            n = len(self._subscribed)
        logger.info("WSPriceMonitor: connected — monitoring %d symbols in real-time", n)

    def _on_error(self, ws, error) -> None:
        logger.warning("WSPriceMonitor: error — %s", error)

    def _on_close(self, ws, code, msg) -> None:
        logger.info("WSPriceMonitor: closed (code=%s)", code)

    def _on_message(self, ws, raw: str) -> None:
        """
        Parse price tick and forward to strategy manager immediately.
        Combined stream format: {"stream":"btcusdt@markPrice","data":{...}}
        """
        try:
            msg  = json.loads(raw)
            data = msg.get("data", msg)     # handle both combined + single stream
            sym  = data.get("s", "")
            # "p" = mark price in markPrice stream
            price_str = data.get("p") or data.get("c") or "0"
            price = float(price_str)
            if sym and price > 0:
                self._sm.realtime_price_update(sym, price)
        except Exception as exc:
            logger.debug("WSPriceMonitor: message parse error — %s", exc)
