"""
Exit Strategy Manager — implements the complete TP-based position management system.

Responsibilities:
  - Detect when a tracked TP level is hit for an open trade
  - Calculate momentum/OI/speed score (0–10) at each TP using snapshot data
  - Execute partial closes (configurable % per score bracket)
  - Ratchet stop-loss upward after each TP
  - Switch to trailing stop at TP30+
  - Enforce time limits (exit if next TP not hit within N hours)
  - Emergency exits: BTC dumps, funding spike, reversal from peak
  - Paper mode: simulate all of the above with zero real orders

Config block (config.json → "exit_strategy"):
  enabled                     — master switch
  initial_sl_pct              — 15.0  (overrides trading.sl_pct when enabled)
  tp5_slow_exit_hours         — 24    (exit 100% if TP5 hit slower than this)
  time_limits_hours           — dict of TP transition limits
  close_pcts                  — exact % to close per TP/score bracket
  sl_ratchet                  — SL price per TP level
  emergency_exit              — btc_dump, funding_spike, reversal rules
  check_interval_seconds      — how often to poll (default 60)
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
    from tracker import SignalTracker

logger = logging.getLogger(__name__)

_SCORED_TPS  = [5, 10, 20, 30]
_TRAIL_TPS   = [30, 50, 75, 100]


class StrategyManager:

    def __init__(
        self,
        config: dict,
        binance: "BinanceClient",
        notifier: "TelegramNotifier",
        tracker: Optional["SignalTracker"] = None,
    ) -> None:
        ec = config.get("exit_strategy", {})
        tc = config.get("trading", {})

        self._enabled: bool       = ec.get("enabled", False)
        self._initial_sl_pct      = float(ec.get("initial_sl_pct", 15.0))
        self._tp5_slow_hours      = float(ec.get("tp5_slow_exit_hours", 24.0))

        # Paper mode — must be read before check_interval so interval selection works
        self._paper_mode: bool              = tc.get("paper_mode", False)
        self._paper_starting_balance: float = float(tc.get("paper_starting_balance", 1000.0))

        _live_interval  = int(ec.get("check_interval_seconds", 60))
        _paper_interval = int(ec.get("paper_check_interval_seconds", 15))
        self._check_interval = _paper_interval if self._paper_mode else _live_interval

        tl = ec.get("time_limits_hours", {})
        self._time_limits = {
            5:  float(tl.get("tp5_to_tp10",  48)) * 3600,
            10: float(tl.get("tp10_to_tp20", 72)) * 3600,
            20: float(tl.get("tp20_to_tp30", 48)) * 3600,
            30: float(tl.get("tp30_to_tp50", 48)) * 3600,
            50: float(tl.get("tp50_to_tp75", 48)) * 3600,
            75: float(tl.get("tp75_to_tp100", 24)) * 3600,
        }

        cp = ec.get("close_pcts", {})
        self._close_pcts = {
            5:  [
                (1, int(cp.get("tp5_score_01", 100))),
                (3, int(cp.get("tp5_score_23", 60))),
                (5, int(cp.get("tp5_score_45", 30))),
                (99, int(cp.get("tp5_score_6plus", 10))),
            ],
            10: [
                (1,  int(cp.get("tp10_score_01", 100))),
                (3,  int(cp.get("tp10_score_23", 50))),
                (5,  int(cp.get("tp10_score_45", 20))),
                (99, int(cp.get("tp10_score_6plus", 10))),
            ],
            20: [
                (2,  int(cp.get("tp20_score_02", 50))),
                (5,  int(cp.get("tp20_score_35", 25))),
                (99, int(cp.get("tp20_score_68", 10))),
            ],
            30: [
                (2,  int(cp.get("tp30_score_02", 100))),
                (5,  int(cp.get("tp30_score_35", 30))),
                (99, int(cp.get("tp30_score_68", 0))),
            ],
            50:  int(cp.get("tp50", 30)),
            75:  int(cp.get("tp75", 30)),
            100: int(cp.get("tp100", 50)),
        }

        sr = ec.get("sl_ratchet", {})
        self._sl_ratchet = {
            "tp5_pct":       float(sr.get("tp5_pct", 0.0)),
            "tp5_ride_keep": bool(sr.get("tp5_ride_keep_sl", True)),
            "tp10_pct":      float(sr.get("tp10_pct", 5.0)),
            "tp10_ride_pct": float(sr.get("tp10_ride_pct", 0.0)),
            "tp20_pct":      float(sr.get("tp20_pct", 12.0)),
            "tp30_trail":    float(sr.get("tp30_trail_pct", 10.0)),
            "tp50_trail":    float(sr.get("tp50_trail_pct", 8.0)),
            "tp75_trail":    float(sr.get("tp75_trail_pct", 8.0)),
            "tp100_trail":   float(sr.get("tp100_trail_pct", 12.0)),
        }

        em = ec.get("emergency_exit", {})
        self._em_btc_dump       = bool(em.get("btc_dump_enabled", True))
        self._em_funding_pct    = float(em.get("funding_spike_pct", 0.3))
        self._em_reversal_pct   = float(em.get("reversal_from_peak_pct", 15.0))

        self._binance  = binance
        self._notifier   = notifier
        self._ws_monitor = None   # set later via set_ws_monitor()
        self._tracker  = tracker
        self._lock     = threading.Lock()
        self._running  = False
        self._last_paper_summary_date = ""

        data_dir = Path(config.get("tracker", {}).get("data_dir", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self._trades_file        = data_dir / "trades.json"
        self._signals_file       = data_dir / "signals.json"
        self._paper_account_file = data_dir / "paper_account.json"

        if self._enabled:
            mode_tag = " [PAPER MODE]" if self._paper_mode else ""
            logger.info(
                "StrategyManager enabled%s  "
                "(init_sl=%.1f%%  tp5_slow=%dh  interval=%ds)",
                mode_tag, self._initial_sl_pct,
                int(self._tp5_slow_hours), self._check_interval,
            )
        else:
            logger.info("StrategyManager disabled  (exit_strategy.enabled = false)")

    # ── properties ───────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def initial_sl_pct(self) -> float:
        return self._initial_sl_pct

    # ── file I/O ─────────────────────────────────────────────────────

    def _load_trades(self) -> list:
        if not self._trades_file.exists():
            return []
        try:
            with open(self._trades_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_trades(self, trades: list) -> None:
        with open(self._trades_file, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)
        self._update_ws_symbols(trades)   # keep WS subscriptions in sync

    def _load_signals(self) -> list:
        if not self._signals_file.exists():
            return []
        try:
            with open(self._signals_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _find_signal(self, signals: list, trade: dict) -> Optional[dict]:
        for s in signals:
            if (s.get("symbol") == trade["symbol"]
                    and s.get("alert_time") == trade.get("signal_time")):
                return s
        return None

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
            data.setdefault("starting_balance", self._paper_starting_balance)
            data.setdefault("current_balance",  self._paper_starting_balance)
            data.setdefault("total_realized_pnl", 0.0)
            return data
        except Exception:
            return {
                "starting_balance": self._paper_starting_balance,
                "current_balance":  self._paper_starting_balance,
                "total_realized_pnl": 0.0,
                "trades_opened": 0,
                "trades_closed": 0,
            }

    def _update_paper_account(self, realized_pnl: float, trade_closed: bool = False) -> tuple:
        """
        Add realized_pnl to paper account. Returns (current_balance, starting_balance).
        """
        account = self._load_paper_account()
        account["total_realized_pnl"] = round(
            account.get("total_realized_pnl", 0.0) + realized_pnl, 4
        )
        account["current_balance"] = round(
            account["starting_balance"] + account["total_realized_pnl"], 4
        )
        if trade_closed:
            account["trades_closed"] = account.get("trades_closed", 0) + 1
        account["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(self._paper_account_file, "w", encoding="utf-8") as f:
            json.dump(account, f, indent=2)
        return account["current_balance"], account["starting_balance"]

    # ── scoring ───────────────────────────────────────────────────────

    def _calc_score_with_breakdown(self, signal: dict, tp: int) -> tuple:
        """
        Returns (score: int, breakdown: list[str]).
        breakdown contains one line per scoring component.
        """
        snap    = signal.get(f"tp{tp}_snapshot", {}) or {}
        outcome = signal.get("outcome", {}) or {}
        score   = 0
        breakdown: list = []
        hours   = outcome.get(f"tp{tp}_hit_hours_after_entry") or 99

        # Speed scoring
        invert = tp >= 30
        if not invert:
            if hours < 3:    score += 3; breakdown.append(f"⚡ Speed {hours:.1f}h → +3")
            elif hours < 6:  score += 2; breakdown.append(f"⚡ Speed {hours:.1f}h → +2")
            elif hours < 12: score += 1; breakdown.append(f"⚡ Speed {hours:.1f}h → +1")
            else:            breakdown.append(f"⚡ Speed {hours:.1f}h → +0")
        else:
            if hours < 12:   score += 2; breakdown.append(f"⚡ Speed {hours:.1f}h → +2")
            elif hours < 48: score += 1; breakdown.append(f"⚡ Speed {hours:.1f}h → +1")
            else:            breakdown.append(f"⚡ Speed {hours:.1f}h → +0")

        m4 = snap.get("price_momentum_4h_pct") or 0
        if m4 >= 8:    score += 2; breakdown.append(f"📈 4h mom {m4:+.1f}% → +2")
        elif m4 >= 3:  score += 1; breakdown.append(f"📈 4h mom {m4:+.1f}% → +1")
        else:          breakdown.append(f"📈 4h mom {m4:+.1f}% → +0")

        m1 = snap.get("price_momentum_1h_pct") or 0
        if m1 >= 5:    score += 2; breakdown.append(f"📈 1h mom {m1:+.1f}% → +2")
        elif m1 >= 2:  score += 1; breakdown.append(f"📈 1h mom {m1:+.1f}% → +1")
        else:          breakdown.append(f"📈 1h mom {m1:+.1f}% → +0")

        oi = snap.get("oi_change_pct") or 0
        if oi >= 15:   score += 2; breakdown.append(f"💹 OI {oi:+.1f}% → +2")
        elif oi >= 5:  score += 1; breakdown.append(f"💹 OI {oi:+.1f}% → +1")
        else:          breakdown.append(f"💹 OI {oi:+.1f}% → +0")

        mcap = snap.get("market_cap_usd")
        if mcap and mcap < 50_000_000:
            score += 1; breakdown.append(f"💰 Mcap <$50M → +1")

        return score, breakdown

    def _calc_score(self, signal: dict, tp: int) -> int:
        score, _ = self._calc_score_with_breakdown(signal, tp)
        return score

    # ── close % lookup ────────────────────────────────────────────────

    def _get_close_pct(self, tp: int, score: int) -> int:
        brackets = self._close_pcts.get(tp)
        if isinstance(brackets, int):
            return brackets
        if isinstance(brackets, list):
            for max_score, pct in brackets:
                if score <= max_score:
                    return pct
        return 0

    # ── SL price calculation ──────────────────────────────────────────

    def _calc_new_sl(
        self,
        tp: int,
        score: int,
        entry: float,
        highest: float,
        current_sl: float,
    ) -> tuple:
        """Returns (new_sl_price, sl_type_str).  None means no change."""
        sr = self._sl_ratchet

        if tp == 5:
            if score >= 6 and sr["tp5_ride_keep"]:
                return None, "fixed"
            new_sl = entry * (1 + sr["tp5_pct"] / 100)
            return max(new_sl, current_sl), "fixed"

        if tp == 10:
            if score >= 6:
                new_sl = entry * (1 + sr["tp10_ride_pct"] / 100)
            else:
                new_sl = entry * (1 + sr["tp10_pct"] / 100)
            return max(new_sl, current_sl), "fixed"

        if tp == 20:
            new_sl = entry * (1 + sr["tp20_pct"] / 100)
            return max(new_sl, current_sl), "fixed"

        if tp == 30:
            if score <= 2:
                return None, "fixed"
            trail_sl = highest * (1 - sr["tp30_trail"] / 100)
            return max(trail_sl, current_sl), f"trailing_{sr['tp30_trail']:.0f}"

        if tp == 50:
            trail_sl = highest * (1 - sr["tp50_trail"] / 100)
            return max(trail_sl, current_sl), f"trailing_{sr['tp50_trail']:.0f}"

        if tp >= 75:
            key = f"tp{tp}_trail" if f"tp{tp}_trail" in sr else "tp75_trail"
            trail_pct = sr.get(key, sr["tp75_trail"])
            trail_sl = highest * (1 - trail_pct / 100)
            return max(trail_sl, current_sl), f"trailing_{trail_pct:.0f}"

        return None, "fixed"

    # ── decision explanation helpers ──────────────────────────────────

    def _explain_close_action(self, tp: int, score: int, close_pct: int) -> str:
        """Generate a human-readable reason for WHY we closed close_pct%."""
        if tp in _SCORED_TPS:
            if close_pct >= 100:
                if score <= 1:
                    return f"Score {score}/10 (very weak) → full exit to protect capital"
                return f"Score {score}/10 (low) → full exit"
            elif close_pct == 0:
                return f"Score {score}/10 (strong) → RIDE — hold full position, update SL only"
            else:
                if score >= 6:
                    return f"Score {score}/10 (strong) → RIDE — close minimum {close_pct}% to take partial profit, hold rest"
                return f"Score {score}/10 (moderate) → partial exit {close_pct}%"
        else:
            return f"TP{tp} fixed rule → close {close_pct}%"

    def _explain_sl_reason(
        self,
        tp: int, score: int,
        new_sl: Optional[float],
        current_sl: float,
        entry: float,
        sl_type: str,
    ) -> str:
        """Generate a human-readable reason for the SL action at this TP."""
        if new_sl is None:
            if tp == 5 and score >= 6:
                return f"SL UNCHANGED — RIDE rule: score {score}/10 ≥ 6 keeps initial SL"
            return "SL UNCHANGED"
        pct_from_entry = (new_sl - entry) / entry * 100 if entry else 0
        if sl_type and "trailing" in sl_type:
            trail_pct = sl_type.replace("trailing_", "")
            return f"SL → TRAILING {trail_pct}% below running high — locks in profit automatically"
        if pct_from_entry >= 0:
            return f"SL raised to +{pct_from_entry:.1f}% from entry — locking in profit"
        return f"SL raised to {pct_from_entry:+.1f}% from entry — reducing max loss"

    # ── order helpers ─────────────────────────────────────────────────

    def _get_position_amt(self, symbol: str) -> float:
        """Return current positionAmt for symbol, or 1.0 if unknown (conservative)."""
        try:
            pos = self._binance.get_position_risk(symbol)
            if pos:
                return float(pos.get("positionAmt", 1.0))
        except Exception:
            pass
        return 1.0

    def _place_reduce_market(
        self,
        symbol: str,
        quantity: float,
        sl_order_id: Optional[int] = None,
        paper: bool = False,
        paper_fill_price: float = 0.0,
    ) -> Optional[dict]:
        """Place a reduce-only SELL MARKET order and confirm it is FILLED.

        In paper mode: skip real API, return a simulated FILLED response using
        paper_fill_price as the avgPrice.

        Returns the filled order dict, or None if placement failed or fill could
        not be confirmed.  Callers MUST check the return value before cancelling
        open orders.

        Special case — SL beat us to it:
          If Binance rejects the reduce-only order and positionAmt is already 0,
          return {"status": "ALREADY_CLOSED", "avgPrice": <sl_fill_price>} so
          callers cancel remaining orders and record accurate PnL.
        """
        if paper:
            return {"status": "FILLED", "avgPrice": str(paper_fill_price or 0)}

        step_size, _ = self._binance.get_symbol_precision(symbol)
        qty = self._round_step(quantity, step_size)
        if qty <= 0:
            logger.warning("SM: close qty rounds to 0 for %s", symbol)
            return None

        order = self._binance.place_market_order_reduce(symbol, "SELL", qty)

        if order is None:
            pos_amt = self._get_position_amt(symbol)
            if abs(pos_amt) == 0:
                avg_price = "0"
                if sl_order_id:
                    sl_data = self._binance.get_order(symbol, sl_order_id)
                    if sl_data and sl_data.get("status") == "FILLED":
                        avg_price = sl_data.get("avgPrice", "0")
                        logger.info("SM: %s SL fill price recovered: %s", symbol, avg_price)
                logger.info(
                    "SM: %s reduce-only SELL rejected — positionAmt is 0, "
                    "SL likely fired during polling window. Treating as clean close.",
                    symbol,
                )
                return {"status": "ALREADY_CLOSED", "avgPrice": avg_price}
            logger.critical(
                "SM: MARKET SELL failed for %s qty=%.4g — "
                "position may still be open, will retry next cycle",
                symbol, qty,
            )
            return None

        status = order.get("status", "")

        if status == "FILLED":
            logger.info("SM: %s MARKET SELL filled (confirmed in response)  avgPrice=%s",
                        symbol, order.get("avgPrice"))
            return order

        if status == "ALREADY_CLOSED":
            return order

        oid = order.get("orderId")
        logger.warning(
            "SM: %s MARKET SELL returned status=%s (expected FILLED) — "
            "polling order %s for fill confirmation",
            symbol, status, oid,
        )
        for attempt in range(1, 6):
            time.sleep(0.3)
            polled = self._binance.get_order_status(symbol, oid) if oid else None
            logger.info("SM: %s fill poll %d/5 → status=%s", symbol, attempt, polled)
            if polled == "FILLED":
                order["status"] = "FILLED"
                return order

        logger.critical(
            "SM: %s MARKET SELL order %s NOT CONFIRMED FILLED after 5 polls — "
            "leaving SL active, will retry close next cycle",
            symbol, oid,
        )
        return None

    def _replace_sl(
        self,
        symbol: str,
        old_order_id: Optional[int],
        new_sl_price: float,
        new_quantity: float,
        paper: bool = False,
    ) -> Optional[int]:
        """Cancel old SL order and place a new one at the new price/qty.
        Returns new order_id, or None if placement failed.
        In paper mode: returns a fake ID (999999) without calling Binance."""
        if paper:
            return 999999

        if old_order_id:
            self._binance.cancel_order(symbol, old_order_id)
        step_size, price_prec = self._binance.get_symbol_precision(symbol)
        sl_price = round(new_sl_price, price_prec)
        qty = self._round_step(new_quantity, step_size)
        if qty <= 0:
            return None
        order = self._binance.place_stop_market_order(symbol, "SELL", qty, sl_price)
        if order is None:
            logger.critical(
                "SM: SL placement FAILED for %s qty=%.4g price=%.8g — "
                "position is running WITHOUT stop-loss protection!",
                symbol, qty, sl_price,
            )
            return None
        return order.get("orderId")

    @staticmethod
    def _round_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        precision = max(0, round(-math.log10(step)))
        return round(math.floor(value / step) * step, precision)

    # ── TP action processor ───────────────────────────────────────────

    def _process_tp_hit(
        self,
        trade: dict,
        signal: dict,
        tp: int,
        trades: list,
        current_price: float = 0.0,
    ) -> bool:
        """
        Execute exit strategy for a newly hit TP level.
        Returns True if trade was fully closed.

        current_price: mark price at time of processing — used as paper fill price
                       and for SL reason computation.
        """
        symbol         = trade["symbol"]
        entry          = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        current_sl     = trade.get("sl_price", 0)
        sl_order_id    = trade.get("sl_order_id")
        remaining      = trade.get("remaining_quantity") or trade.get("quantity", 0)
        orig_qty       = trade.get("quantity", remaining)
        outcome        = signal.get("outcome", {}) or {}
        now_ts         = time.time()
        paper          = trade.get("paper", False)

        if remaining <= 0:
            return False

        tp_hours = outcome.get(f"tp{tp}_hit_hours_after_entry") or 0

        # ── TP5 special: slow exit ─────────────────────────────────────
        if tp == 5 and tp_hours >= self._tp5_slow_hours:
            logger.info(
                "SM: %s TP5 slow exit — took %.1fh (≥%.0fh) — closing 100%% at market",
                symbol, tp_hours, self._tp5_slow_hours,
            )
            order = self._place_reduce_market(
                symbol, remaining, sl_order_id=sl_order_id,
                paper=paper, paper_fill_price=current_price,
            )
            if order is None:
                return False
            if not paper:
                self._binance.cancel_all_open_orders(symbol)

            close_price = (float(order.get("avgPrice", 0)) if order else 0) or entry
            now = datetime.now(timezone.utc)
            trade["remaining_quantity"]  = 0
            trade["remaining_pct"]       = 0
            trade["sl_order_id"]         = None
            trade["status"]              = "closed_tp_exit"
            trade["close_reason"]        = f"tp5_slow_exit_{tp_hours:.0f}h"
            trade["closed_at"]           = now.strftime("%Y-%m-%d %H:%M:%S UTC")
            trade["close_price"]         = close_price
            trade["pnl_pct"]             = round((close_price - entry) / entry * 100, 2) if entry else 0
            trade["pnl_usdt"]            = round((close_price - entry) * remaining, 2) if entry else 0

            processed = trade.get("processed_tps") or []
            processed.append("tp5")
            trade["processed_tps"]       = processed
            trade["time_limit_expires_ts"] = None

            action_str = f"EXIT 100% — TP5 took {tp_hours:.1f}h (≥{self._tp5_slow_hours:.0f}h slow rule)"

            decision = {
                "tp": tp, "score": 0, "close_pct": 100,
                "action_str": action_str,
                "action_reason": f"TP5 slow-hit rule (≥{self._tp5_slow_hours:.0f}h) → full exit to protect capital",
                "close_price": close_price,
                "close_qty": remaining,
                "partial_pnl_usdt": round(trade["pnl_usdt"], 4),
                "partial_pnl_pct":  round(trade["pnl_pct"], 4),
                "new_sl": None,
                "sl_changed": False,
                "sl_reason": "SL cancelled — trade closed",
                "tp_hours": tp_hours,
                "remaining_pct": 0,
                "paper": paper,
            }
            if paper:
                bal, bal_s = self._update_paper_account(trade["pnl_usdt"], trade_closed=True)
                decision["paper_balance"] = bal
                decision["paper_balance_start"] = bal_s
            self._store_tp_decision(trade, signal, tp, decision)

            self._notifier.send_tp_action(
                trade, 5, 0, 100, action_str, slow_exit=True, decision=decision,
            )
            return True

        # ── score-based exit ──────────────────────────────────────────
        score, score_breakdown = self._calc_score_with_breakdown(signal, tp)
        close_pct  = self._get_close_pct(tp, score)
        close_qty  = remaining * (close_pct / 100.0)

        if tp in _SCORED_TPS:
            action_str = f"Score {score}/10 → Close {close_pct}%"
        else:
            action_str = f"TP{tp} hit → Close {close_pct}%"

        action_reason = self._explain_close_action(tp, score, close_pct)

        logger.info(
            "SM: %s TP%d  score=%d  close_pct=%d%%  qty=%.4g  action=%s",
            symbol, tp, score, close_pct, close_qty, action_str,
        )

        fully_closed  = False
        close_price   = current_price or entry
        new_sl_price  = None
        new_sl_type   = "fixed"
        sl_changed    = False
        partial_pnl_usdt = 0.0
        partial_pnl_pct  = 0.0

        highest = signal.get("highest_price", entry) or entry

        if close_pct >= 100:
            order = self._place_reduce_market(
                symbol, remaining, sl_order_id=sl_order_id,
                paper=paper, paper_fill_price=current_price,
            )
            if order is None:
                return False
            if not paper:
                self._binance.cancel_all_open_orders(symbol)

            close_price = (float(order.get("avgPrice", 0)) if order else 0) or entry
            partial_pnl_usdt = round((close_price - entry) * remaining, 4) if entry else 0
            partial_pnl_pct  = round((close_price - entry) / entry * 100, 2) if entry else 0

            trade["remaining_quantity"] = 0
            trade["sl_order_id"]        = None
            trade["status"]             = "closed_tp_exit"
            trade["close_reason"]       = f"tp{tp}_strategy"
            trade["closed_at"]          = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            trade["close_price"]        = close_price
            trade["pnl_pct"]            = partial_pnl_pct
            trade["pnl_usdt"]           = partial_pnl_usdt
            fully_closed = True

        elif close_pct > 0:
            order = self._place_reduce_market(
                symbol, close_qty, sl_order_id=sl_order_id,
                paper=paper, paper_fill_price=current_price,
            )
            if order is not None:
                close_price = (float(order.get("avgPrice", 0)) if order else 0) or entry
                partial_pnl_usdt = round((close_price - entry) * close_qty, 4) if entry else 0
                partial_pnl_pct  = round((close_price - entry) / entry * 100, 2) if entry else 0

            new_remaining = remaining - close_qty
            trade["remaining_quantity"] = new_remaining

            new_sl_price, new_sl_type = self._calc_new_sl(tp, score, entry, highest, current_sl)
            if new_sl_price is not None:
                new_oid = self._replace_sl(
                    symbol, sl_order_id, new_sl_price, new_remaining, paper=paper,
                )
                trade["sl_price"]    = round(new_sl_price, 8)
                trade["sl_order_id"] = new_oid
                trade["sl_type"]     = new_sl_type
                sl_changed = True
            else:
                new_oid = self._replace_sl(
                    symbol, sl_order_id, current_sl, new_remaining, paper=paper,
                )
                trade["sl_order_id"] = new_oid

        else:
            # close_pct == 0 → update SL only (trail)
            new_sl_price, new_sl_type = self._calc_new_sl(tp, score, entry, highest, current_sl)
            if new_sl_price is not None:
                new_oid = self._replace_sl(
                    symbol, sl_order_id, new_sl_price, remaining, paper=paper,
                )
                trade["sl_price"]    = round(new_sl_price, 8)
                trade["sl_order_id"] = new_oid
                trade["sl_type"]     = new_sl_type
                sl_changed = True

        # ── record processed TP ───────────────────────────────────────
        processed = trade.get("processed_tps") or []
        processed.append(f"tp{tp}")
        trade["processed_tps"] = processed

        # ── set time limit for next TP ────────────────────────────────
        expires_str = ""
        if not fully_closed and tp in self._time_limits:
            trade["time_limit_expires_ts"] = now_ts + self._time_limits[tp]
            expires_str = datetime.fromtimestamp(
                trade["time_limit_expires_ts"], tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            trade["time_limit_expires_str"] = expires_str
        else:
            trade["time_limit_expires_ts"] = None

        # ── compute remaining_pct ─────────────────────────────────────
        new_remaining = trade.get("remaining_quantity", 0)
        remaining_pct = round(new_remaining / orig_qty * 100) if orig_qty > 0 else 0
        trade["remaining_pct"] = remaining_pct
        effective_sl  = trade.get("sl_price", current_sl)
        sl_reason     = self._explain_sl_reason(
            tp, score, new_sl_price, current_sl, entry, new_sl_type,
        )

        # ── build decision dict ───────────────────────────────────────
        decision = {
            "tp":               tp,
            "score":            score,
            "close_pct":        close_pct,
            "action_str":       action_str,
            "action_reason":    action_reason,
            "close_price":      close_price,
            "close_qty":        round(close_qty, 8),
            "partial_pnl_usdt": partial_pnl_usdt,
            "partial_pnl_pct":  partial_pnl_pct,
            "new_sl":           round(new_sl_price, 8) if new_sl_price else None,
            "sl_changed":       sl_changed,
            "sl_reason":        sl_reason,
            "time_limit_str":   expires_str,
            "tp_hours":         tp_hours,
            "remaining_pct":    remaining_pct,
            "paper":            paper,
        }

        # Update paper account with partial PnL
        if paper and partial_pnl_usdt != 0:
            bal, bal_s = self._update_paper_account(
                partial_pnl_usdt, trade_closed=fully_closed,
            )
            decision["paper_balance"]       = bal
            decision["paper_balance_start"] = bal_s

        # Store decision in trade record
        if "tp_decisions" not in trade:
            trade["tp_decisions"] = {}
        trade["tp_decisions"][f"tp{tp}"] = decision

        # Store decision in signal snapshot (via tracker for thread safety)
        self._store_tp_decision(trade, signal, tp, decision)

        # ── send rich Telegram notification ──────────────────────────
        self._notifier.send_tp_action(
            trade, tp, score, close_pct, action_str,
            slow_exit=False,
            score_breakdown=score_breakdown,
            decision=decision,
        )

        return fully_closed

    def _store_tp_decision(self, trade: dict, signal: dict, tp: int, decision: dict) -> None:
        """Store decision in signal snapshot. Uses tracker (thread-safe) if available,
        otherwise updates the in-memory signal dict directly."""
        if self._tracker is not None:
            symbol      = trade["symbol"]
            signal_time = trade.get("signal_time", "")
            try:
                self._tracker.store_tp_decision(symbol, signal_time, tp, decision)
            except Exception as exc:
                logger.warning("SM: store_tp_decision failed: %s", exc)
        else:
            # Fallback: update in-memory signal dict
            snap_key = f"tp{tp}_snapshot"
            snap = signal.get(snap_key) or {}
            snap["decision"] = decision
            signal[snap_key] = snap

    # ── paper SL hit handler ──────────────────────────────────────────

    def _handle_paper_sl_hit(self, trade: dict, current_price: float) -> None:
        """Close a paper trade when price drops to or below sl_price."""
        symbol    = trade["symbol"]
        entry     = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        remaining = trade.get("remaining_quantity") or 0
        sl_price  = trade.get("sl_price", 0)
        fill_price = sl_price if sl_price > 0 else current_price

        pnl_usdt = round((fill_price - entry) * remaining, 4) if entry else 0
        pnl_pct  = round((fill_price - entry) / entry * 100, 2) if entry else 0

        now = datetime.now(timezone.utc)
        trade["remaining_quantity"] = 0
        trade["sl_order_id"]        = None
        trade["status"]             = "closed_sl"
        trade["close_reason"]       = "sl_hit"
        trade["closed_at"]          = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        trade["close_price"]        = fill_price
        trade["pnl_pct"]            = pnl_pct
        trade["pnl_usdt"]           = pnl_usdt

        bal, bal_s = self._update_paper_account(pnl_usdt, trade_closed=True)

        logger.info(
            "SM[paper]: %s SL hit  price=%.8g  sl=%.8g  pnl=%.2f%%",
            symbol, current_price, sl_price, pnl_pct,
        )
        self._notifier.send_paper_sl_hit(trade, bal)

    # ── time limit enforcement ────────────────────────────────────────

    def _check_time_limits(
        self, trade: dict, signal: dict, current_price: float = 0.0,
    ) -> bool:
        """
        Exit remaining position if time limit for next TP has expired.
        Returns True if trade was closed.
        """
        expires = trade.get("time_limit_expires_ts")
        if not expires:
            return False
        if time.time() < expires:
            return False

        symbol    = trade["symbol"]
        remaining = trade.get("remaining_quantity") or 0
        entry     = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        paper     = trade.get("paper", False)

        if remaining <= 0:
            return False

        processed = trade.get("processed_tps") or []
        last_tp   = max((int(p.replace("tp", "")) for p in processed if p.startswith("tp")), default=0)

        logger.info(
            "SM: %s time limit expired after TP%d — exiting remaining %.4g",
            symbol, last_tp, remaining,
        )

        order = self._place_reduce_market(
            symbol, remaining, sl_order_id=trade.get("sl_order_id"),
            paper=paper, paper_fill_price=current_price,
        )
        if order is None:
            logger.critical("SM: %s time-limit close fill unconfirmed — SL stays active, retrying next cycle", symbol)
            return False
        if not paper:
            self._binance.cancel_all_open_orders(symbol)

        close_price = (float(order.get("avgPrice", 0)) if order else 0) or entry
        now = datetime.now(timezone.utc)
        trade["remaining_quantity"]    = 0
        trade["sl_order_id"]           = None
        trade["status"]                = "closed_time_limit"
        trade["close_reason"]          = f"time_limit_after_tp{last_tp}"
        trade["closed_at"]             = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        trade["close_price"]           = close_price
        trade["time_limit_expires_ts"] = None
        trade["pnl_pct"]               = round((close_price - entry) / entry * 100, 2) if entry else 0
        trade["pnl_usdt"]              = round((close_price - entry) * remaining, 2) if entry else 0
        trade["_last_tp_for_msg"]      = last_tp

        if paper:
            self._update_paper_account(trade["pnl_usdt"], trade_closed=True)

        self._notifier.send_time_limit_exit(trade)
        return True

    # ── trailing stop management ──────────────────────────────────────

    def _update_trailing_sl(self, trade: dict, signal: dict, current_price: float) -> bool:
        """
        Update trailing SL price as price makes new highs.
        Trigger exit if current price drops below trailing SL.
        Returns True if exit was triggered.
        """
        sl_type = trade.get("sl_type", "fixed")
        if not sl_type.startswith("trailing"):
            return False

        try:
            trail_pct = float(sl_type.split("_")[1])
        except (IndexError, ValueError):
            trail_pct = 10.0

        symbol     = trade["symbol"]
        remaining  = trade.get("remaining_quantity") or 0
        entry      = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        current_sl = trade.get("sl_price", 0)
        paper      = trade.get("paper", False)

        if remaining <= 0:
            return False

        running_high = trade.get("trail_high") or current_price
        if current_price > running_high:
            running_high = current_price
            trade["trail_high"] = running_high

            new_sl = running_high * (1 - trail_pct / 100)
            if new_sl > current_sl:
                if not paper:
                    _, price_prec = self._binance.get_symbol_precision(symbol)
                    new_sl = round(new_sl, price_prec)
                new_oid = self._replace_sl(
                    symbol, trade.get("sl_order_id"), new_sl, remaining, paper=paper,
                )
                trade["sl_price"]    = new_sl
                trade["sl_order_id"] = new_oid
                logger.info("SM: %s trail SL raised to %.8g (high=%.8g  trail=%.1f%%)",
                            symbol, new_sl, running_high, trail_pct)

        if current_price <= current_sl:
            logger.info(
                "SM: %s trailing SL triggered  price=%.8g  sl=%.8g",
                symbol, current_price, current_sl,
            )
            order = self._place_reduce_market(
                symbol, remaining, sl_order_id=trade.get("sl_order_id"),
                paper=paper, paper_fill_price=current_price,
            )
            if order is None:
                logger.critical("SM: %s trailing-SL close fill unconfirmed — SL stays active, retrying next cycle", symbol)
                return False
            if not paper:
                self._binance.cancel_all_open_orders(symbol)

            close_price = (float(order.get("avgPrice", 0)) if order else 0) or current_price
            pnl_usdt = round((close_price - entry) * remaining, 2) if entry else 0
            now = datetime.now(timezone.utc)
            trade["remaining_quantity"] = 0
            trade["sl_order_id"]        = None
            trade["status"]             = "closed_trail_sl"
            trade["close_reason"]       = "trailing_stop"
            trade["closed_at"]          = now.strftime("%Y-%m-%d %H:%M:%S UTC")
            trade["close_price"]        = close_price
            trade["pnl_pct"]            = round((close_price - entry) / entry * 100, 2) if entry else 0
            trade["pnl_usdt"]           = pnl_usdt

            if paper:
                self._update_paper_account(pnl_usdt, trade_closed=True)

            self._notifier.send_trade_closed(trade)
            return True

        return False

    # ── emergency exits ───────────────────────────────────────────────

    def _check_emergency(self, trade: dict, current_price: float, signal: dict) -> bool:
        """Check emergency exit conditions. Returns True if exit was triggered."""
        symbol    = trade["symbol"]
        remaining = trade.get("remaining_quantity") or 0
        entry     = trade.get("actual_entry_price") or trade.get("entry_price", 0)

        if remaining <= 0:
            return False

        highest = signal.get("highest_price") or current_price
        if highest > 0 and current_price < highest * (1 - self._em_reversal_pct / 100):
            logger.warning(
                "SM: %s EMERGENCY — price %.8g reversed %.1f%% from peak %.8g",
                symbol, current_price,
                (1 - current_price / highest) * 100, highest,
            )
            self._emergency_close(trade, current_price, "reversal_from_peak")
            return True

        paper = trade.get("paper", False)
        if not paper and self._em_funding_pct > 0:
            fr = self._binance.get_funding_rate(symbol)
            if fr is not None and fr * 100 > self._em_funding_pct:
                logger.warning(
                    "SM: %s EMERGENCY — funding %.4f%% > %.2f%%",
                    symbol, fr * 100, self._em_funding_pct,
                )
                self._emergency_close(trade, current_price, "funding_spike")
                return True

        return False

    def _emergency_close(self, trade: dict, current_price: float, reason: str) -> None:
        symbol    = trade["symbol"]
        remaining = trade.get("remaining_quantity") or 0
        entry     = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        paper     = trade.get("paper", False)

        if remaining <= 0:
            return
        order = self._place_reduce_market(
            symbol, remaining, sl_order_id=trade.get("sl_order_id"),
            paper=paper, paper_fill_price=current_price,
        )
        if order is None:
            logger.critical("SM: %s emergency close fill unconfirmed — SL stays active, retrying next cycle", symbol)
            return
        if not paper:
            self._binance.cancel_all_open_orders(symbol)

        close_price = (float(order.get("avgPrice", 0)) if order else 0) or current_price
        pnl_usdt = round((close_price - entry) * remaining, 2) if entry else 0
        now = datetime.now(timezone.utc)
        trade["remaining_quantity"] = 0
        trade["sl_order_id"]        = None
        trade["status"]             = "closed_emergency"
        trade["close_reason"]       = reason
        trade["closed_at"]          = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        trade["close_price"]        = close_price
        trade["pnl_pct"]            = round((close_price - entry) / entry * 100, 2) if entry else 0
        trade["pnl_usdt"]           = pnl_usdt

        if paper:
            self._update_paper_account(pnl_usdt, trade_closed=True)

        self._notifier.send_trade_closed(trade)

    # ── TP100 candle-based exit ───────────────────────────────────────

    def _check_tp100_candle_exit(
        self, trade: dict, signal: dict, current_price: float = 0.0,
    ) -> bool:
        """
        After TP100 is hit, exit remaining position when 2 consecutive
        closed 4h candles are red (close < open).
        Returns True if exit was triggered.
        """
        processed = set(trade.get("processed_tps") or [])
        if "tp100" not in processed:
            return False

        remaining = trade.get("remaining_quantity") or 0
        if remaining <= 0:
            return False

        symbol = trade["symbol"]
        entry  = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        paper  = trade.get("paper", False)

        try:
            candles = self._binance.get_closed_klines(symbol, "4h", 4)
            if len(candles) < 2:
                return False
            c1 = candles[-2]
            c2 = candles[-3]
            c1_red = float(c1["close"]) < float(c1["open"])
            c2_red = float(c2["close"]) < float(c2["open"])
        except Exception:
            logger.exception("SM: %s TP100 candle check error", symbol)
            return False

        if not (c1_red and c2_red):
            return False

        logger.info("SM: %s TP100 candle exit — 2 consecutive red 4h candles detected", symbol)

        order = self._place_reduce_market(
            symbol, remaining, sl_order_id=trade.get("sl_order_id"),
            paper=paper, paper_fill_price=current_price,
        )
        if order is None:
            logger.critical("SM: %s TP100-candle close fill unconfirmed — SL stays active, retrying next cycle", symbol)
            return False
        if not paper:
            self._binance.cancel_all_open_orders(symbol)

        if paper and current_price > 0:
            fill_price = current_price
        else:
            fetched = self._binance.get_mark_price_single(symbol)
            fill_price = float(fetched or entry)

        close_price = (float(order.get("avgPrice", 0)) if order else 0) or fill_price
        pnl_usdt = round((close_price - entry) * remaining, 2) if entry else 0
        now = datetime.now(timezone.utc)
        trade["remaining_quantity"] = 0
        trade["sl_order_id"]        = None
        trade["status"]             = "closed_tp100_candle"
        trade["close_reason"]       = "tp100_two_red_4h_candles"
        trade["closed_at"]          = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        trade["close_price"]        = close_price
        trade["pnl_pct"]            = round((close_price - entry) / entry * 100, 2) if entry else 0
        trade["pnl_usdt"]           = pnl_usdt

        if paper:
            self._update_paper_account(pnl_usdt, trade_closed=True)

        pnl_icon = "📈" if trade["pnl_pct"] >= 0 else "📉"
        prefix = "📝 PAPER " if paper else ""
        self._notifier.send(
            f"{prefix}🕯 <b>TP100 EXIT — 2 RED 4H CANDLES</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>\n"
            f"💵 Entry:   {entry:.8g}\n"
            f"💵 Close:   {close_price:.8g}\n"
            f"{pnl_icon} PnL:     {trade['pnl_pct']:+.2f}%\n"
            f"🕐 Closed:  {trade['closed_at']}"
        )
        return True

    # ── BTC dump watcher ─────────────────────────────────────────────

    def _check_btc_dump(self, trades: list, prices: dict) -> bool:
        """
        If BTC is dumping, move SL to -5% from current price on ALL open trades.
        Only triggers once per trade (flag: btc_dump_sl_applied).
        Returns True if any trade was modified (so caller can persist to disk).
        """
        if not self._em_btc_dump:
            return False

        btc = prices.get("BTCUSDT")
        if not btc:
            return False

        try:
            candles = self._binance.get_closed_klines("BTCUSDT", "4h", 7)
            if len(candles) < 7:
                return
            price_4h_ago  = candles[-2]["close"]
            price_24h_ago = candles[-7]["close"]
            chg_4h  = (btc - price_4h_ago) / price_4h_ago * 100
            chg_24h = (btc - price_24h_ago) / price_24h_ago * 100
        except Exception:
            return False

        is_dumping = chg_4h < -3.0 and chg_24h < -3.0
        if not is_dumping:
            return False

        modified = False
        sl_moved_lines = []   # collect all SL moves → single Telegram message
        for trade in trades:
            if trade.get("status") != "open":
                continue
            if trade.get("btc_dump_sl_applied"):
                continue

            symbol    = trade["symbol"]
            cur_p     = prices.get(symbol, 0)
            paper     = trade.get("paper", False)
            if cur_p <= 0:
                continue

            new_sl    = cur_p * 0.95
            remaining = trade.get("remaining_quantity") or trade.get("quantity", 0)
            sl_oid    = trade.get("sl_order_id")

            if not paper:
                _, price_prec = self._binance.get_symbol_precision(symbol)
                new_sl_r = round(new_sl, price_prec)
            else:
                new_sl_r = round(new_sl, 8)

            # FIX: only tighten the SL — never loosen a ratcheted stop
            current_sl = trade.get("sl_price", 0)
            if new_sl_r <= current_sl:
                logger.warning(
                    "SM: BTC DUMP — %s SL NOT moved (current %.8g already tighter than dump SL %.8g)",
                    symbol, current_sl, new_sl_r,
                )
                trade["btc_dump_sl_applied"] = True
                modified = True   # still need to save flag to disk
                continue

            new_oid = self._replace_sl(symbol, sl_oid, new_sl_r, remaining, paper=paper)
            trade["sl_price"]            = new_sl_r
            trade["sl_order_id"]         = new_oid
            trade["btc_dump_sl_applied"] = True

            modified = True
            logger.warning(
                "SM: BTC DUMP — %s SL moved to -5%% from current: %.8g",
                symbol, new_sl_r,
            )
            # collect for single consolidated message (not per-trade spam)
            sl_moved_lines.append(
                f"  📌 <b>{symbol}</b>  SL: {new_sl_r:.6g}  "
                f"(cur: {cur_p:.6g}  dist: {((cur_p - new_sl_r)/cur_p*100):.1f}% away)"
            )

        # ── Send ONE consolidated alert instead of one per trade ──────────
        if sl_moved_lines:
            prefix = "📝 PAPER " if any(t.get("paper") for t in trades) else ""
            header = (
                f"{prefix}⚠️ <b>BTC DUMP — SL ADJUSTED ({len(sl_moved_lines)} trades)</b>\n"
                f"₿ BTC 4h: {chg_4h:+.1f}%  24h: {chg_24h:+.1f}%\n\n"
            )
            self._notifier.send(header + "\n".join(sl_moved_lines))

        return modified   # caller uses this to trigger _save_trades immediately

    # ── daily paper summary ───────────────────────────────────────────

    def _maybe_send_paper_daily_summary(self, trades: list) -> None:
        """Send a daily paper trading summary at midnight UTC."""
        if not self._paper_mode:
            return
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour != 0:
            return
        today_str = now_utc.strftime("%Y-%m-%d")
        if self._last_paper_summary_date == today_str:
            return
        self._last_paper_summary_date = today_str

        paper_trades = [t for t in trades if t.get("paper")]
        if not paper_trades:
            return

        today_date_str = now_utc.strftime("%Y-%m-%d")
        closed_today = [
            t for t in paper_trades
            if (t.get("closed_at", "") or "").startswith(today_date_str[:10])
            and t.get("status", "").startswith("closed")
        ]
        open_paper = [t for t in paper_trades if t.get("status") == "open"]

        total     = len(closed_today)
        tp5_hit   = sum(1 for t in closed_today if "tp5"  in (t.get("processed_tps") or []))
        tp10_hit  = sum(1 for t in closed_today if "tp10" in (t.get("processed_tps") or []))
        sl_hit    = sum(1 for t in closed_today if t.get("close_reason") == "sl_hit")
        tl_hit    = sum(1 for t in closed_today if "time_limit" in (t.get("close_reason") or ""))
        total_pnl = sum(t.get("pnl_usdt") or 0 for t in closed_today)

        account = self._load_paper_account()
        stats = {
            "total_today":     total,
            "tp5_hit":         tp5_hit,
            "tp10_hit":        tp10_hit,
            "sl_hit":          sl_hit,
            "time_limit_hit":  tl_hit,
            "total_pnl_usdt":  total_pnl,
            "starting_balance": account.get("starting_balance", self._paper_starting_balance),
            "current_balance":  account.get("current_balance", self._paper_starting_balance),
        }
        self._notifier.send_paper_daily_summary(stats, open_paper)

    # ── main loop ─────────────────────────────────────────────────────

    def _process_once(self) -> None:
        # In paper mode, skip the credentials guard — no real API calls needed for execution
        if not self._paper_mode and not self._binance.has_trading_credentials():
            return

        with self._lock:
            trades  = self._load_trades()
            signals = self._load_signals()

            open_trades = [
                t for t in trades
                if t.get("status") == "open" and (t.get("remaining_quantity") or t.get("quantity", 0)) > 0
            ]
            if not open_trades:
                return

            prices  = self._binance.get_mark_prices()
            changed = False

            if self._check_btc_dump(open_trades, prices):
                changed = True
                # Save IMMEDIATELY so flag persists even if per-trade loop throws.
                # Without this, an exception in the loop skips _save_trades at
                # the bottom → disk still has btc_dump_sl_applied=False →
                # same coin fires again on the next 15s cycle.
                self._save_trades(trades)

            for trade in open_trades:
                symbol  = trade["symbol"]
                signal  = self._find_signal(signals, trade)
                if signal is None:
                    continue

                current = prices.get(symbol, 0)
                outcome = signal.get("outcome", {}) or {}
                paper   = trade.get("paper", False)

                # Initialise exit_strategy fields if first time seeing this trade
                if "remaining_quantity" not in trade:
                    trade["remaining_quantity"]  = trade.get("quantity", 0)
                    trade["processed_tps"]       = []
                    trade["tp_decisions"]        = {}
                    trade["sl_type"]             = "fixed"
                    trade["trail_high"]          = current
                    trade["time_limit_expires_ts"] = None

                # ── paper SL detection (no real orders to detect for paper trades) ──
                if paper and current > 0:
                    sl_price = trade.get("sl_price", 0)
                    if sl_price > 0 and current <= sl_price:
                        self._handle_paper_sl_hit(trade, current)
                        changed = True
                        continue



                processed = set(trade.get("processed_tps") or [])

                # Check each TP level in order
                fully_closed = False
                for tp in [5, 10, 20, 30, 50, 75, 100]:
                    tp_key = f"tp{tp}"
                    if tp_key in processed:
                        continue
                    if not outcome.get(f"tp{tp}_hit", False):
                        break  # TPs are sequential — stop at first unhit

                    fully_closed = self._process_tp_hit(
                        trade, signal, tp, trades, current_price=current,
                    )
                    changed = True
                    if fully_closed:
                        break

                if fully_closed:
                    continue

                # Time limit check
                if self._check_time_limits(trade, signal, current_price=current):
                    changed = True
                    continue

                # Trailing SL update
                if current > 0:
                    if self._update_trailing_sl(trade, signal, current):
                        changed = True
                        continue

                # TP100 two-red-4h-candles exit
                if self._check_tp100_candle_exit(trade, signal, current_price=current):
                    changed = True
                    continue

                # Emergency exit check
                if current > 0:
                    if self._check_emergency(trade, current, signal):
                        changed = True

            if changed:
                self._save_trades(trades)

            # Daily paper summary check (outside changed guard — runs regardless)
            self._maybe_send_paper_daily_summary(trades)


    def set_ws_monitor(self, monitor) -> None:
        """Called from main.py after WSPriceMonitor is created."""
        self._ws_monitor = monitor

    def _update_ws_symbols(self, trades: list) -> None:
        """Push current open trade symbols to the WebSocket monitor."""
        if self._ws_monitor is None:
            return
        open_syms = {
            t["symbol"] for t in trades
            if t.get("status") == "open"
            and (t.get("remaining_quantity") or t.get("quantity", 0)) > 0
        }
        self._ws_monitor.update_symbols(open_syms)

    # ── WebSocket real-time price update ─────────────────────────────
    #
    # Called by WSPriceMonitor on every 1-second mark-price tick.
    # Runs the SAME per-trade logic as _process_once but instantly,
    # bypassing the 300s tracker poll and the 15-60s SM poll cycle.
    #
    # TP levels are computed directly from entry_price — no need to
    # wait for tracker.py to detect and write them to signals.json.
    # ─────────────────────────────────────────────────────────────────

    def _save_signals(self, signals: list) -> None:
        """Write signals back to disk (used by realtime_price_update)."""
        try:
            with open(self._signals_file, "w", encoding="utf-8") as f:
                json.dump(signals, f, indent=2)
        except Exception as exc:
            logger.warning("SM: could not save signals — %s", exc)

    def realtime_price_update(self, symbol: str, price: float) -> None:
        """
        Entry point called by WSPriceMonitor (~every 1 second per symbol).

        1. Computes TP levels from entry_price directly (no tracker needed).
        2. If a new TP is crossed, marks it in the signal immediately.
        3. Runs the full trade-management logic (same as _process_once per trade).
        4. Saves if anything changed.
        """
        try:
            with self._lock:
                self._realtime_tick(symbol, price)
        except Exception:
            logger.exception("SM: realtime_price_update error for %s", symbol)

    def _realtime_tick(self, symbol: str, price: float) -> None:
        """Internal — runs under self._lock."""
        trades  = self._load_trades()
        signals = self._load_signals()

        open_trades = [
            t for t in trades
            if t.get("symbol") == symbol
            and t.get("status") == "open"
            and (t.get("remaining_quantity") or t.get("quantity", 0)) > 0
        ]
        if not open_trades:
            return

        trades_changed  = False
        signals_changed = False

        for trade in open_trades:
            signal = self._find_signal(signals, trade)
            if signal is None:
                continue

            entry  = trade.get("actual_entry_price") or trade.get("entry_price", 0)
            paper  = trade.get("paper", False)
            if not entry:
                continue

            outcome = signal.setdefault("outcome", {})

            # ── update highest_price in signal ──────────────────────
            prev_high = outcome.get("highest_price") or signal.get("highest_price") or entry
            if price > prev_high:
                outcome["highest_price"] = price
                signal["highest_price"]  = price
                signals_changed = True

            # ── detect NEW TP hits from current price ────────────────
            opened_ts    = trade.get("opened_ts", time.time())
            hours_open   = (time.time() - opened_ts) / 3600.0
            processed_set = set(trade.get("processed_tps") or [])

            for tp in [5, 10, 20, 30, 50, 75, 100]:
                tp_key   = f"tp{tp}"
                hit_key  = f"tp{tp}_hit"
                if tp_key in processed_set:
                    continue                              # already acted on
                if outcome.get(hit_key):
                    continue                              # tracker already flagged it
                tp_price = entry * (1 + tp / 100.0)
                if price >= tp_price:
                    # TP crossed in real-time — record it
                    outcome[hit_key] = True
                    outcome[f"tp{tp}_hit_time"] = (
                        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    )
                    outcome[f"tp{tp}_hit_hours_after_entry"] = round(hours_open, 2)
                    # Minimal snapshot (tracker will enrich on next cycle)
                    if not signal.get(f"tp{tp}_snapshot"):
                        signal[f"tp{tp}_snapshot"] = {
                            "price":                  price,
                            "oi_change_pct":          None,
                            "price_momentum_4h_pct":  None,
                            "price_momentum_1h_pct":  None,
                            "market_cap_usd":         (
                                signal.get("additional_data") or {}
                            ).get("market_cap_usd"),
                        }
                    signals_changed = True
                    logger.info(
                        "SM[WS]: %s TP%d HIT in real-time  price=%.8g  entry=%.8g",
                        symbol, tp, price, entry,
                    )

            # ── initialise trade fields if first time ───────────────
            if "remaining_quantity" not in trade:
                trade["remaining_quantity"]    = trade.get("quantity", 0)
                trade["processed_tps"]         = []
                trade["tp_decisions"]          = {}
                trade["sl_type"]               = "fixed"
                trade["trail_high"]            = price
                trade["time_limit_expires_ts"] = None

            # ── paper SL check ───────────────────────────────────────
            if paper and price > 0:
                sl_price = trade.get("sl_price", 0)
                if sl_price > 0 and price <= sl_price:
                    self._handle_paper_sl_hit(trade, price)
                    trades_changed = True
                    continue

            # ── TP ladder ────────────────────────────────────────────
            fully_closed = False
            for tp in [5, 10, 20, 30, 50, 75, 100]:
                tp_key = f"tp{tp}"
                if tp_key in set(trade.get("processed_tps") or []):
                    continue
                if not outcome.get(f"tp{tp}_hit", False):
                    break
                fully_closed = self._process_tp_hit(
                    trade, signal, tp, trades, current_price=price
                )
                trades_changed = True
                if fully_closed:
                    break

            if fully_closed:
                continue

            # ── time limit ───────────────────────────────────────────
            if self._check_time_limits(trade, signal, current_price=price):
                trades_changed = True
                continue

            # ── trailing SL update ───────────────────────────────────
            if price > 0 and self._update_trailing_sl(trade, signal, price):
                trades_changed = True
                continue

            # ── emergency exit ───────────────────────────────────────
            if price > 0 and self._check_emergency(trade, price, signal):
                trades_changed = True

        if trades_changed:
            self._save_trades(trades)
        if signals_changed:
            self._save_signals(signals)

    def run(self) -> None:
        self._running = True
        logger.info("StrategyManager loop started (every %ds)", self._check_interval)
        while self._running:
            try:
                self._process_once()
            except Exception:
                logger.exception("StrategyManager loop error")
            time.sleep(self._check_interval)

    def stop(self) -> None:
        self._running = False
