"""
Automated trader — places and monitors Binance USDT-M Futures positions.

Config block (config.json → "trading"):
  enabled                — true/false master switch
  paper_mode             — true = simulate without real orders
  paper_starting_balance — fake starting balance for paper mode (USDT)
  margin_type            — "pct" (% of free balance) or "fixed" (USDT amount)
  margin_value           — numeric value for margin
  leverage               — preferred leverage to try first
  leverage_step          — reduction amount if preferred leverage unavailable
  leverage_min           — minimum acceptable leverage (signal skipped if below)
  sl_pct                 — stop-loss distance from entry (%)
  max_open_trades        — hard cap on concurrent open positions
  check_interval_seconds — how often to poll Binance for manual closes (default 60)

Flow per signal (live):
  1. Guard: trading enabled, API creds set, symbol not already open, max not reached
  2. Set leverage with fallback (e.g. 20 → 15 → 10 → 5)
  3. Fetch USDT free balance
  4. Calculate margin and quantity
  5. Place MARKET BUY order
  6. Place STOP_MARKET SELL (reduce-only) for stop loss
  7. Save trade record to data/trades.json
  8. Send Telegram notification

Flow per signal (paper mode):
  - All Binance order calls are SKIPPED
  - Real price data still used for entry price
  - Paper trade record saved with paper=true flag
  - Telegram notified with 📝 PAPER prefix

Manual-close detection (background loop):
  - Live trades: polls positionRisk for all open trades; positionAmt==0 → closed
  - Paper trades: SL detection handled by StrategyManager (price vs sl_price check)
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from binance_client import BinanceClient
    from notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class Trader:

    def __init__(
        self,
        config: dict,
        binance: "BinanceClient",
        notifier: "TelegramNotifier",
    ) -> None:
        tc = config.get("trading", {})
        self._enabled: bool          = tc.get("enabled", False)
        self._paper_mode: bool       = tc.get("paper_mode", False)
        self._paper_starting_balance: float = float(tc.get("paper_starting_balance", 1000.0))
        self._margin_type: str       = tc.get("margin_type", "pct")
        self._margin_value: float    = float(tc.get("margin_value", 5))
        self._leverage: int          = int(tc.get("leverage", 20))
        self._leverage_step: int     = int(tc.get("leverage_step", 5))
        self._leverage_min: int      = int(tc.get("leverage_min", 5))
        self._sl_pct: float          = float(tc.get("sl_pct", 3.0))
        self._max_open: int          = int(tc.get("max_open_trades", 5))
        self._check_interval: int    = int(tc.get("check_interval_seconds", 60))

        self._binance  = binance
        self._notifier = notifier
        self._lock     = threading.Lock()
        self._running  = False

        data_dir = Path(config.get("tracker", {}).get("data_dir", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self._trades_file       = data_dir / "trades.json"
        self._paper_account_file = data_dir / "paper_account.json"

        if self._enabled:
            mode_tag = " [PAPER MODE]" if self._paper_mode else ""
            logger.info(
                "Trader enabled%s  (margin=%s%s  lev=%d step=%d min=%d  sl=%.1f%%  max=%d  poll=%ds)",
                mode_tag,
                self._margin_value,
                "%" if self._margin_type == "pct" else "USDT",
                self._leverage, self._leverage_step, self._leverage_min,
                self._sl_pct, self._max_open, self._check_interval,
            )
            if self._paper_mode:
                logger.info(
                    "Paper mode: starting balance=$%.2f",
                    self._paper_starting_balance,
                )
        else:
            logger.info("Trader disabled  (trading.enabled = false)")

    # ── file I/O ─────────────────────────────────────────────────────

    def _load(self) -> list:
        if not self._trades_file.exists():
            return []
        try:
            with open(self._trades_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, trades: list) -> None:
        with open(self._trades_file, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)

    # ── paper account I/O ─────────────────────────────────────────────

    def _load_paper_account(self) -> dict:
        if not self._paper_account_file.exists():
            return {
                "starting_balance": self._paper_starting_balance,
                "current_balance":  self._paper_starting_balance,
                "total_realized_pnl": 0.0,
                "trades_opened": 0,
                "trades_closed": 0,
            }
        try:
            with open(self._paper_account_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Ensure all keys exist (in case file was created by older version)
            data.setdefault("starting_balance", self._paper_starting_balance)
            data.setdefault("current_balance", self._paper_starting_balance)
            data.setdefault("total_realized_pnl", 0.0)
            data.setdefault("trades_opened", 0)
            data.setdefault("trades_closed", 0)
            return data
        except Exception:
            return {
                "starting_balance": self._paper_starting_balance,
                "current_balance":  self._paper_starting_balance,
                "total_realized_pnl": 0.0,
                "trades_opened": 0,
                "trades_closed": 0,
            }

    def _save_paper_account(self, account: dict) -> None:
        account["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(self._paper_account_file, "w", encoding="utf-8") as f:
            json.dump(account, f, indent=2)

    # ── helpers ──────────────────────────────────────────────────────

    def _open_count(self, trades: list) -> int:
        return sum(1 for t in trades if t.get("status") == "open")

    def _already_open(self, symbol: str, trades: list) -> bool:
        return any(
            t.get("symbol") == symbol and t.get("status") == "open"
            for t in trades
        )

    @staticmethod
    def _round_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        precision = max(0, round(-math.log10(step)))
        return round(math.floor(value / step) * step, precision)

    # ── leverage fallback ─────────────────────────────────────────────

    def _set_leverage(self, symbol: str) -> Optional[int]:
        lev = self._leverage
        while lev >= self._leverage_min:
            result = self._binance.set_leverage(symbol, lev)
            if result is not None:
                logger.info("Trader: %s leverage set to %dx", symbol, result)
                return result
            lev_next = lev - self._leverage_step
            logger.info(
                "Trader: %s leverage %dx unavailable → trying %dx",
                symbol, lev, lev_next,
            )
            lev = lev_next
        logger.warning("Trader: %s could not set leverage ≥ %dx — skipping", symbol, self._leverage_min)
        return None

    # ── public: place trade ───────────────────────────────────────────

    def place_trade(self, symbol: str, entry_price: float, alert: dict) -> None:
        """Called by scanner immediately after a signal fires."""
        if not self._enabled:
            return

        if entry_price <= 0:
            logger.warning("Trader: invalid entry_price %.8g for %s — skipping", entry_price, symbol)
            return

        if self._paper_mode:
            self._place_paper_trade(symbol, entry_price, alert)
            return

        # ── live trading path ──────────────────────────────────────────
        if not self._binance.has_trading_credentials():
            logger.warning(
                "Trader: BINANCE_API_KEY / BINANCE_API_SECRET not set — "
                "set them to enable live trading"
            )
            return

        with self._lock:
            trades = self._load()

            if self._already_open(symbol, trades):
                logger.info("Trader: %s already has an open trade — skipping", symbol)
                return

            open_n = self._open_count(trades)
            if open_n >= self._max_open:
                logger.info(
                    "Trader: max open trades reached (%d/%d) — skipping %s",
                    open_n, self._max_open, symbol,
                )
                return

            actual_lev = self._set_leverage(symbol)
            if actual_lev is None:
                return

            balance = self._binance.get_usdt_balance()
            if balance is None or balance <= 0:
                logger.warning("Trader: could not fetch USDT balance — skipping %s", symbol)
                return

            if self._margin_type == "pct":
                margin_usdt = balance * (self._margin_value / 100.0)
            else:
                margin_usdt = float(self._margin_value)

            if margin_usdt <= 0:
                logger.warning("Trader: margin computed as 0 — skipping %s", symbol)
                return

            step_size, price_prec = self._binance.get_symbol_precision(symbol)
            position_value = margin_usdt * actual_lev
            quantity = self._round_step(position_value / entry_price, step_size)

            if quantity <= 0:
                logger.warning(
                    "Trader: quantity rounds to 0 for %s "
                    "(margin=$%.2f lev=%d price=%.8g step=%g) — skipping",
                    symbol, margin_usdt, actual_lev, entry_price, step_size,
                )
                return

            logger.info(
                "Trader: placing %s LONG  qty=%g  margin=$%.2f  lev=%dx  sl=%.1f%%",
                symbol, quantity, margin_usdt, actual_lev, self._sl_pct,
            )

            order = self._binance.place_market_order(symbol, "BUY", quantity)
            if order is None:
                logger.error("Trader: market order failed for %s", symbol)
                return

            order_id     = order.get("orderId")
            actual_price = float(order.get("avgPrice", 0) or 0) or entry_price

            sl_price    = round(actual_price * (1 - self._sl_pct / 100.0), price_prec)
            sl_order    = self._binance.place_stop_market_order(symbol, "SELL", quantity, sl_price)
            sl_order_id = sl_order.get("orderId") if sl_order else None

            if sl_order_id is None:
                logger.warning("Trader: SL order failed for %s — position is open WITHOUT stop loss!", symbol)

            now = datetime.now(timezone.utc)
            trade = {
                "trade_id":                f"{symbol}_{int(now.timestamp())}",
                "symbol":                  symbol,
                "signal_time":             alert.get("alert_time", ""),
                "entry_price":             entry_price,
                "actual_entry_price":      actual_price,
                "quantity":                quantity,
                "remaining_quantity":      quantity,
                "margin_used":             round(margin_usdt, 2),
                "leverage":                actual_lev,
                "sl_pct":                  self._sl_pct,
                "sl_price":                sl_price,
                "sl_order_id":             sl_order_id,
                "sl_type":                 "fixed",
                "trail_high":              actual_price,
                "entry_order_id":          order_id,
                "opened_at":               now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "opened_ts":               now.timestamp(),
                "status":                  "open",
                "processed_tps":           [],
                "tp_decisions":            {},
                "time_limit_expires_ts":   None,
                "time_limit_expires_str":  None,
                "btc_dump_sl_applied":     False,
                "close_reason":            None,
                "close_price":             None,
                "closed_at":               None,
                "pnl_pct":                 None,
                "pnl_usdt":                None,
                "paper":                   False,
            }

            trades.append(trade)
            self._save(trades)

            logger.info(
                "Trader: ✅ %s trade opened  entry=$%.8g  sl=$%.8g  order=%s",
                symbol, actual_price, sl_price, order_id,
            )
            self._notifier.send_trade_opened(trade)

    # ── paper trade placement ─────────────────────────────────────────

    def _place_paper_trade(self, symbol: str, entry_price: float, alert: dict) -> None:
        """Simulate a trade entry without placing any real orders."""
        with self._lock:
            trades = self._load()

            if self._already_open(symbol, trades):
                logger.info("Trader[paper]: %s already has an open trade — skipping", symbol)
                return

            open_n = self._open_count(trades)
            if open_n >= self._max_open:
                logger.info(
                    "Trader[paper]: max open trades reached (%d/%d) — skipping %s",
                    open_n, self._max_open, symbol,
                )
                return

            # Use paper account balance for margin calculation
            account = self._load_paper_account()
            balance = account["current_balance"]

            if self._margin_type == "pct":
                margin_usdt = balance * (self._margin_value / 100.0)
            else:
                margin_usdt = float(self._margin_value)

            if margin_usdt <= 0:
                logger.warning("Trader[paper]: margin computed as 0 — skipping %s", symbol)
                return

            actual_lev  = self._leverage
            step_size, price_prec = self._binance.get_symbol_precision(symbol)
            # In paper mode, use step_size=0 fallback if precision lookup fails
            if step_size <= 0:
                step_size = 0.001

            position_value = margin_usdt * actual_lev
            quantity = self._round_step(position_value / entry_price, step_size) if step_size > 0 else round(position_value / entry_price, 6)

            if quantity <= 0:
                logger.warning(
                    "Trader[paper]: quantity rounds to 0 for %s "
                    "(margin=$%.2f lev=%d price=%.8g) — skipping",
                    symbol, margin_usdt, actual_lev, entry_price,
                )
                return

            sl_price = round(entry_price * (1 - self._sl_pct / 100.0), price_prec if price_prec > 0 else 8)

            now = datetime.now(timezone.utc)
            trade = {
                "trade_id":                f"paper_{symbol}_{int(now.timestamp())}",
                "symbol":                  symbol,
                "signal_time":             alert.get("alert_time", ""),
                "entry_price":             entry_price,
                "actual_entry_price":      entry_price,
                "quantity":                quantity,
                "remaining_quantity":      quantity,
                "margin_used":             round(margin_usdt, 2),
                "leverage":                actual_lev,
                "sl_pct":                  self._sl_pct,
                "sl_price":                sl_price,
                "sl_order_id":             None,
                "sl_type":                 "fixed",
                "trail_high":              entry_price,
                "entry_order_id":          None,
                "opened_at":               now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "opened_ts":               now.timestamp(),
                "status":                  "open",
                "processed_tps":           [],
                "tp_decisions":            {},
                "time_limit_expires_ts":   None,
                "time_limit_expires_str":  None,
                "btc_dump_sl_applied":     False,
                "close_reason":            None,
                "close_price":             None,
                "closed_at":               None,
                "pnl_pct":                 None,
                "pnl_usdt":                None,
                "paper":                   True,
                "_max_open":               self._max_open,
            }

            trades.append(trade)
            self._save(trades)

            # Update paper account trades_opened count
            account["trades_opened"] = account.get("trades_opened", 0) + 1
            self._save_paper_account(account)

            logger.info(
                "Trader[paper]: 📝 %s paper trade opened  entry=$%.8g  sl=$%.8g  margin=$%.2f",
                symbol, entry_price, sl_price, margin_usdt,
            )
            self._notifier.send_paper_trade_opened(trade, account["current_balance"])

    # ── public: check for manual closes (live only) ───────────────────

    def check_positions(self) -> None:
        """
        Poll Binance positionRisk for all LIVE open trades.
        Paper trades are monitored by StrategyManager (price vs sl_price).
        """
        if not self._enabled:
            return
        if self._paper_mode:
            return
        if not self._binance.has_trading_credentials():
            return

        with self._lock:
            trades  = self._load()
            open_ts = [t for t in trades if t.get("status") == "open" and not t.get("paper")]
            if not open_ts:
                return

            changed = False
            for trade in open_ts:
                symbol = trade["symbol"]
                try:
                    pos = self._binance.get_position_risk(symbol)
                    if pos is None:
                        continue

                    pos_amt = float(pos.get("positionAmt", 1))
                    if abs(pos_amt) > 0:
                        continue

                    close_reason = "manual"
                    sl_oid = trade.get("sl_order_id")
                    if sl_oid:
                        sl_status = self._binance.get_order_status(symbol, sl_oid)
                        if sl_status == "FILLED":
                            close_reason = "sl_hit"
                        else:
                            self._binance.cancel_order(symbol, sl_oid)

                    entry       = trade.get("actual_entry_price") or trade.get("entry_price", 0)
                    close_price = self._binance.get_mark_price_single(symbol) or entry
                    pnl_pct     = ((close_price - entry) / entry * 100) if entry > 0 else 0
                    pnl_usdt    = (close_price - entry) * trade.get("quantity", 0) if entry > 0 else 0

                    now = datetime.now(timezone.utc)
                    trade["status"]       = f"closed_{close_reason}"
                    trade["close_reason"] = close_reason
                    trade["close_price"]  = close_price
                    trade["closed_at"]    = now.strftime("%Y-%m-%d %H:%M:%S UTC")
                    trade["pnl_pct"]      = round(pnl_pct, 2)
                    trade["pnl_usdt"]     = round(pnl_usdt, 2)
                    changed = True

                    logger.info(
                        "Trader: %s closed (%s)  entry=%.8g  close=%.8g  pnl=%.2f%%",
                        symbol, close_reason, entry, close_price, pnl_pct,
                    )
                    self._notifier.send_trade_closed(trade)

                except Exception:
                    logger.exception("Trader: error checking position for %s", symbol)

            if changed:
                self._save(trades)

    # ── background monitoring loop ────────────────────────────────────

    def run(self) -> None:
        self._running = True
        logger.info("Trader monitoring loop started (poll every %ds)", self._check_interval)
        while self._running:
            try:
                self.check_positions()
            except Exception:
                logger.exception("Trader monitoring loop error")
            time.sleep(self._check_interval)

    def stop(self) -> None:
        self._running = False

    # ── public getters ────────────────────────────────────────────────

    def get_open_trades(self) -> list:
        return [t for t in self._load() if t.get("status") == "open"]

    def get_all_trades(self) -> list:
        return self._load()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode
