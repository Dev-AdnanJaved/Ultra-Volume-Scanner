"""
Telegram Bot API helper.

Sends:
  - Breakout alerts (signal entry)
  - Take-profit target hit alerts
  - Reversal warning alerts
  - Trade opened / closed notifications
  - TP action alerts with full decision breakdown (paper and live)
  - Paper trading specific messages
  - Startup summary
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TelegramNotifier:
    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, bot_token: str, chat_id: str):
        self._token = bot_token
        self._chat_id = chat_id
        self._session = requests.Session()
        self._ok = False

    def _url(self, method: str) -> str:
        return self.API.format(token=self._token, method=method)

    @staticmethod
    def _clean(value) -> str:
        """Escape HTML special chars in any dynamic string value."""
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def validate(self) -> bool:
        try:
            r = self._session.get(self._url("getMe"), timeout=10).json()
            if r.get("ok"):
                logger.info("Telegram bot validated: @%s", r["result"].get("username"))
                self._ok = True
                return True
            logger.error("Telegram validation failed: %s", r)
        except Exception as exc:
            logger.error("Telegram validation error: %s", exc)
        return False

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        for attempt in range(3):
            try:
                r = self._session.post(
                    self._url("sendMessage"),
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                ).json()
                if r.get("ok"):
                    return True
                if r.get("error_code") == 429:
                    wait = r.get("parameters", {}).get("retry_after", 30)
                    logger.warning("Telegram 429 — waiting %ds", wait)
                    time.sleep(wait)
                    continue
                desc = r.get("description", "")
                if parse_mode and "parse entities" in desc.lower():
                    logger.error("Unexpected Telegram HTML parse error — retrying plain text. desc=%s", desc)
                    return self.send(text, parse_mode=None)
                logger.error("Telegram error: %s", r)
                return False
            except Exception as exc:
                logger.error("Telegram send failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
        return False

    def send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a file as a Telegram document."""
        for attempt in range(3):
            try:
                with open(file_path, "rb") as f:
                    r = self._session.post(
                        self._url("sendDocument"),
                        data={"chat_id": self._chat_id, "caption": caption},
                        files={"document": f},
                        timeout=30,
                    ).json()
                if r.get("ok"):
                    return True
                if r.get("error_code") == 429:
                    wait = r.get("parameters", {}).get("retry_after", 30)
                    time.sleep(wait)
                    continue
                logger.error("Telegram send_document error: %s", r)
                return False
            except Exception as exc:
                logger.error("Telegram send_document failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
        return False

    # ── alert types ──────────────────────────────────────────────────

    def send_alert(self, data: dict) -> bool:
        return self.send(self._fmt_alert(data))

    def send_startup(self, summary: str) -> bool:
        return self.send(
            f"🤖 <b>Volume Scanner Started</b>\n\n{summary}\n\nScanner is now running …"
        )

    def send_take_profit(self, data: dict) -> bool:
        return self.send(self._fmt_take_profit(data))

    def send_reversal_warning(self, data: dict) -> bool:
        return self.send(self._fmt_reversal(data))

    # ── price formatting ─────────────────────────────────────────────

    @staticmethod
    def _fp(price: float) -> str:
        if price <= 0:
            return "N/A"
        if price >= 1000:
            return f"${price:,.2f}"
        if price >= 1:
            return f"${price:.4f}"
        if price >= 0.001:
            return f"${price:.6f}"
        return f"${price:.8f}"

    # ── signal alert format ──────────────────────────────────────────

    def _fmt_alert(self, d: dict) -> str:
        symbol = d["symbol"]
        tf = d.get("timeframe", "1h")
        price = d.get("price", "N/A")
        brk_margin = d.get("breakout_margin_pct", 0)
        price_chg = d.get("price_change_24h", 0)
        v1 = d.get("vol_candle_1_fmt", "?")
        v2 = d.get("vol_candle_2_fmt", "?")
        v3 = d.get("vol_candle_3_fmt", "?")
        bv1 = d.get("vol_candle_1_base_fmt", "?")
        bv2 = d.get("vol_candle_2_base_fmt", "?")
        bv3 = d.get("vol_candle_3_base_fmt", "?")
        rvol = d.get("rvol", 0)
        alert_time = d.get("alert_time", "N/A")
        cooldown = d.get("cooldown_hours", 12)

        chg_icon = "🟢" if price_chg >= 0 else "🔴"
        high_brk = d.get("high_breakout_warning", False)

        vol_24h_usdt = d.get("additional_data", {}).get("vol_24h_usdt", 0) or 0
        is_premium = vol_24h_usdt >= 20_000_000

        btc_trend = d.get("btc_trend", "unknown")
        btc_detail = d.get("btc_trend_detail", {})
        btc_icons = {"ranging": "🟢", "pumping": "🟡", "dumping": "🔴", "unknown": "❓"}
        btc_labels = {"ranging": "RANGING ✓", "pumping": "PUMPING", "dumping": "DUMPING", "unknown": "UNKNOWN"}
        btc_icon = btc_icons.get(btc_trend, "❓")
        btc_label = btc_labels.get(btc_trend, "UNKNOWN")

        if high_brk:
            header = "⚠️ <b>BREAKOUT SIGNAL — HIGH BREAKOUT</b>"
        elif is_premium:
            header = "💎 <b>BREAKOUT SIGNAL — PREMIUM</b>"
        else:
            header = "🚨 <b>BREAKOUT SIGNAL</b>"

        base_coin = symbol.replace("USDT", "").replace("BUSD", "")

        cv1, cv2, cv3   = self._clean(v1),  self._clean(v2),  self._clean(v3)
        cbv1, cbv2, cbv3 = self._clean(bv1), self._clean(bv2), self._clean(bv3)

        lines = [
            header,
            f"{'━' * 28}",
            "",
            f"📌 <b>{symbol}</b>  |  {tf}" + ("  💎 <b>PREMIUM</b>" if is_premium else ""),
            f"💵 <b>Price:</b>  ${price}",
            "",
            f"1️⃣ <b>Breakout:</b>  +{brk_margin:.2f}% above 24h high",
            f"2️⃣ <b>Vol USDT:</b>  {cv1} → {cv2} → {cv3}  ({rvol:.1f}x avg)",
            f"    <b>Vol {base_coin}:</b>  {cbv1} → {cbv2} → {cbv3}",
            f"3️⃣ <b>24h Change:</b>  {chg_icon} {price_chg:+.1f}%",
            "",
        ]

        btc_chg_4h = btc_detail.get("btc_chg_4h")
        btc_chg_24h = btc_detail.get("btc_chg_24h")
        if btc_chg_4h is not None:
            lines.append(f"₿ <b>BTC Trend:</b>  {btc_icon} {btc_label}  (4h: {btc_chg_4h:+.2f}%  24h: {btc_chg_24h:+.2f}%)")
            lines.append("")

        if high_brk:
            lines.append(f"⚠️ <b>Warning:</b> Breakout margin {brk_margin:.2f}% > 5% — enter with caution")
            lines.append("")

        q_score = d.get("quality_score", "?")
        s_flags = d.get("soft_flags", 0)
        sf_details = d.get("soft_flag_details", [])
        q_details = d.get("quality_details", [])

        if q_score >= 7:
            grade = "🟢 EXCELLENT"
        elif q_score >= 5:
            grade = "🟢 STRONG"
        elif q_score >= 4:
            grade = "🟡 GOOD"
        elif q_score >= 2:
            grade = "🟠 FAIR"
        else:
            grade = "🔴 WEAK"

        lines.append(f"⭐ <b>Quality:</b>  {q_score}/8  {grade}")
        if s_flags > 0:
            lines.append(f"🚩 <b>Warnings:</b>  {s_flags}/8  ({', '.join(self._clean(f) for f in sf_details)})")
        else:
            lines.append(f"🚩 <b>Warnings:</b>  0/8")
        lines.append("")

        lines.extend([
            f"🕐 <b>Time:</b>  {alert_time}",
            f"⏱ <b>Cooldown:</b>  {cooldown}h",
        ])
        return "\n".join(lines)

    # ── take-profit alert format ─────────────────────────────────────

    def _fmt_take_profit(self, d: dict) -> str:
        target = d["target"]
        if target >= 75:
            icon = "💎🚀🚀"
        elif target >= 50:
            icon = "🚀🚀🚀"
        elif target >= 30:
            icon = "🚀🚀"
        elif target >= 10:
            icon = "🚀"
        elif target >= 5:
            icon = "🎯"
        else:
            icon = "✅"

        cur_pct = d.get("cur_pct", 0)
        high_pct = d.get("high_pct", 0)
        age = d.get("age_str", "")

        return (
            f"{icon} <b>TARGET HIT  +{target}%</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{d['symbol']}</b>\n"
            f"💵 Entry:    {self._fp(d['entry_price'])}\n"
            f"🏔  Peak:     {self._fp(d['highest_price'])}  (+{high_pct:.2f}%)\n"
            f"💵 Now:      {self._fp(d['current_price'])}  ({cur_pct:+.2f}%)\n"
            f"⏱  Age:      {age}\n\n"
            f"{'🟢 Still above target' if cur_pct >= target else '⚠️ Price pulled back from target'}"
        )

    # ── trade notifications ───────────────────────────────────────────

    def send_trade_opened(self, trade: dict) -> bool:
        symbol   = trade["symbol"]
        lev      = trade["leverage"]
        price    = trade["actual_entry_price"]
        sl       = trade["sl_price"]
        sl_pct   = trade["sl_pct"]
        margin   = trade["margin_used"]
        qty      = trade["quantity"]
        text = (
            f"🟢 <b>TRADE OPENED</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>  ·  {lev}x LONG\n"
            f"💵 Entry:      {self._fp(price)}\n"
            f"🛡 Stop Loss:  {self._fp(sl)}  (-{sl_pct:.1f}%)\n"
            f"💰 Margin:     ${margin:.2f}\n"
            f"📦 Quantity:   {qty:g}\n"
            f"🕐 Time:       {trade.get('opened_at', '')}"
        )
        return self.send(text)

    def send_trade_closed(self, trade: dict) -> bool:
        symbol  = trade["symbol"]
        reason  = trade.get("close_reason", "unknown")
        entry   = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        close   = trade.get("close_price", 0) or 0
        pnl_pct = trade.get("pnl_pct", 0) or 0
        pnl_usd = trade.get("pnl_usdt", 0) or 0
        paper   = trade.get("paper", False)

        reason_map = {
            "sl_hit":           ("🔴", "STOP LOSS HIT"),
            "manual":           ("🔵", "MANUALLY CLOSED"),
            "trailing_stop":    ("🟠", "TRAILING STOP HIT"),
            "reversal_from_peak": ("🟠", "EMERGENCY — REVERSAL"),
            "funding_spike":    ("🟠", "EMERGENCY — FUNDING SPIKE"),
        }
        icon, label = reason_map.get(reason, ("🔵", reason.upper().replace("_", " ")))
        pnl_icon = "📈" if pnl_pct >= 0 else "📉"
        prefix = "📝 PAPER " if paper else ""
        text = (
            f"{prefix}{icon} <b>TRADE CLOSED — {label}</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>\n"
            f"💵 Entry:   {self._fp(entry)}\n"
            f"💵 Close:   {self._fp(close)}\n"
            f"{pnl_icon} PnL:     {pnl_pct:+.2f}%  (${pnl_usd:+.2f})\n"
            f"🕐 Closed:  {trade.get('closed_at', '')}"
        )
        return self.send(text)

    def send_tp_action(
        self,
        trade: dict,
        tp: int,
        score: int,
        close_pct: int,
        action_str: str,
        slow_exit: bool = False,
        score_breakdown: Optional[list] = None,
        decision: Optional[dict] = None,
    ) -> bool:
        """
        Rich TP action alert. Works for both paper and live trades.

        decision dict keys (all optional):
          close_price, close_qty, partial_pnl_usdt, partial_pnl_pct,
          new_sl, sl_changed, sl_reason, time_limit_str, tp_hours,
          remaining_pct, paper_balance, paper_balance_start
        """
        symbol    = trade["symbol"]
        entry     = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        new_sl    = trade.get("sl_price", 0)
        remaining = trade.get("remaining_quantity", 0)
        orig_qty  = trade.get("quantity", remaining)
        paper     = trade.get("paper", False)
        d         = decision or {}

        remain_pct = d.get("remaining_pct") or (
            round(remaining / orig_qty * 100) if orig_qty > 0 else 0
        )
        expires_str = d.get("time_limit_str") or trade.get("time_limit_expires_str", "")
        tp_limit_h  = {5: 48, 10: 72, 20: 48, 30: 48, 50: 48, 75: 24}.get(tp, "—")
        tp_hours    = d.get("tp_hours", 0)

        # ── header ──
        if close_pct >= 100:
            icon   = "🔴" if slow_exit else "🟠"
            header = f"TP{tp} — FULL EXIT"
        elif close_pct == 0:
            icon   = "🟢"
            header = f"TP{tp} — TRAIL ONLY"
        else:
            icon   = "🟡"
            header = f"TP{tp} — PARTIAL CLOSE ({close_pct}%)"

        prefix = "📝 PAPER " if paper else ""
        lines = [
            f"{prefix}{icon} <b>{header}</b>",
            f"{'━' * 28}",
            "",
        ]

        tp_hours_str = f" in {tp_hours:.1f}h" if tp_hours else ""
        lines.append(f"📌 <b>{symbol}</b>  ·  TP{tp} hit (+{tp}%){tp_hours_str}")
        lines.append("")

        # ── score breakdown ──
        if tp in (5, 10, 20, 30) and not slow_exit:
            lines.append(f"📊 <b>Score: {score}/10</b>")
            if score_breakdown:
                for item in score_breakdown:
                    lines.append(f"   {self._clean(item)}")
            lines.append("")

        # ── decision / action ──
        lines.append(f"✅ <b>Action:</b> {self._clean(action_str)}")
        if slow_exit:
            lines.append(f"   Reason: TP5 slow-hit rule (≥24h) → full exit to protect capital")
        elif d.get("action_reason"):
            lines.append(f"   Reason: {self._clean(d['action_reason'])}")
        lines.append("")

        # ── fill details (if any close happened) ──
        if close_pct > 0:
            close_p  = d.get("close_price", 0)
            close_q  = d.get("close_qty", 0)
            pnl_usd  = d.get("partial_pnl_usdt", 0)
            pnl_pct_ = d.get("partial_pnl_pct", 0)
            pnl_icon = "📈" if pnl_usd >= 0 else "📉"
            if close_p:
                lines.append(f"💵 Entry:        {self._fp(entry)}")
                lines.append(f"💵 Closed at:    {self._fp(close_p)}  ({pnl_pct_:+.2f}%)")
                if close_q:
                    lines.append(f"📦 Closed qty:   {close_q:g}  {pnl_icon} ${pnl_usd:+.4g}")
                lines.append("")

        # ── remaining position ──
        if close_pct < 100:
            lines.append(f"🏦 <b>Remaining:</b> {remain_pct}% open  ({remaining:g} qty)")

        # ── SL status ──
        sl_reason = d.get("sl_reason", "")
        if d.get("sl_changed"):
            lines.append(f"🛡 <b>SL moved to:</b> {self._fp(new_sl)}")
        else:
            lines.append(f"🛡 <b>SL:</b> {self._fp(new_sl)} (unchanged)")
        if sl_reason:
            lines.append(f"   {self._clean(sl_reason)}")

        # ── time limit ──
        if expires_str and close_pct < 100:
            lines.append("")
            lines.append(f"⏱ <b>Next TP window:</b> {tp_limit_h}h → exit by {expires_str}")

        # ── paper balance ──
        if paper:
            bal     = d.get("paper_balance", 0)
            bal_s   = d.get("paper_balance_start", 0)
            if bal and bal_s:
                delta = bal - bal_s
                lines.append("")
                lines.append(
                    f"💰 <b>Paper balance:</b> ${bal:.2f} / ${bal_s:.2f} start "
                    f"({delta:+.2f})"
                )

        return self.send("\n".join(lines))

    def send_time_limit_exit(self, trade: dict) -> bool:
        symbol   = trade["symbol"]
        entry    = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        close    = trade.get("close_price", 0) or 0
        pnl_pct  = trade.get("pnl_pct", 0) or 0
        pnl_usd  = trade.get("pnl_usdt", 0) or 0
        last_tp  = trade.get("_last_tp_for_msg", 0)
        paper    = trade.get("paper", False)
        pnl_icon = "📈" if pnl_pct >= 0 else "📉"
        next_tp  = {5: 10, 10: 20, 20: 30, 30: 50, 50: 75, 75: 100}.get(last_tp, "next")
        prefix   = "📝 PAPER " if paper else ""
        text = (
            f"{prefix}⏱ <b>TIME LIMIT EXIT — TP{next_tp} not reached</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>\n"
            f"⏰ Held past TP{last_tp} time window — exiting remaining position\n\n"
            f"💵 Entry:   {self._fp(entry)}\n"
            f"💵 Close:   {self._fp(close)}\n"
            f"{pnl_icon} PnL:     {pnl_pct:+.2f}%  (${pnl_usd:+.2f})\n"
            f"🕐 Closed:  {trade.get('closed_at', '')}"
        )
        return self.send(text)

    # ── paper-specific notifications ──────────────────────────────────

    def send_paper_trade_opened(self, trade: dict, paper_balance: float) -> bool:
        symbol  = trade["symbol"]
        lev     = trade.get("leverage", 1)
        price   = trade.get("actual_entry_price", 0)
        sl      = trade.get("sl_price", 0)
        sl_pct  = trade.get("sl_pct", 0)
        margin  = trade.get("margin_used", 0)
        max_op  = trade.get("_max_open", 5)
        sl_usdt = abs((price - sl) / price * margin * lev) if price else 0
        text = (
            f"📝 <b>PAPER TRADE OPENED</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>  ·  {lev}x LONG\n"
            f"💵 Entry:          {self._fp(price)}\n"
            f"🛡 SL set at:      {self._fp(sl)}  (-{sl_pct:.1f}%)\n"
            f"   Max loss:       ${sl_usdt:.2f}\n"
            f"💰 Margin (fake):  ${margin:.2f}\n"
            f"💰 Paper balance:  ${paper_balance:.2f}\n"
            f"📊 Max concurrent: {max_op} slots\n"
            f"🕐 Time:           {trade.get('opened_at', '')}"
        )
        return self.send(text)

    def send_paper_sl_hit(self, trade: dict, paper_balance: float) -> bool:
        symbol  = trade["symbol"]
        entry   = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        sl_p    = trade.get("close_price", trade.get("sl_price", 0))
        pnl_pct = trade.get("pnl_pct", 0) or 0
        pnl_usd = trade.get("pnl_usdt", 0) or 0
        text = (
            f"📝 🔴 <b>PAPER SL HIT</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{symbol}</b>\n"
            f"💵 Entry:      {self._fp(entry)}\n"
            f"🔴 SL hit at:  {self._fp(sl_p)}\n"
            f"📉 Loss:       {pnl_pct:+.2f}%  (${pnl_usd:+.2f})\n"
            f"🏦 Remaining:  $0 (closed)\n"
            f"💰 Paper balance: ${paper_balance:.2f}\n"
            f"🕐 Closed:     {trade.get('closed_at', '')}"
        )
        return self.send(text)

    def send_paper_daily_summary(self, stats: dict, open_trades: list) -> bool:
        total       = stats.get("total_today", 0)
        tp5_hit     = stats.get("tp5_hit", 0)
        tp10_hit    = stats.get("tp10_hit", 0)
        sl_hit      = stats.get("sl_hit", 0)
        tl_hit      = stats.get("time_limit_hit", 0)
        total_pnl   = stats.get("total_pnl_usdt", 0.0)
        start_bal   = stats.get("starting_balance", 1000.0)
        cur_bal     = stats.get("current_balance", start_bal)
        pnl_pct_tot = (total_pnl / start_bal * 100) if start_bal else 0
        pnl_icon    = "📈" if total_pnl >= 0 else "📉"

        tp5_pct  = f"{tp5_hit}/{total} ({tp5_hit/total*100:.0f}%)" if total else "0/0"
        tp10_pct = f"{tp10_hit}/{total} ({tp10_hit/total*100:.0f}%)" if total else "0/0"
        sl_pct_s = f"{sl_hit}/{total} ({sl_hit/total*100:.0f}%)"   if total else "0/0"

        lines = [
            "📊 <b>PAPER TRADING DAILY SUMMARY</b>",
            f"{'━' * 28}",
            f"Trades today:     {total}",
            f"TP5 hit:          {tp5_pct}",
            f"TP10 hit:         {tp10_pct}",
            f"SL hits:          {sl_pct_s}",
            f"Time limits hit:  {tl_hit}/{total}" if total else f"Time limits hit:  0/0",
            f"{pnl_icon} Total paper PnL: ${total_pnl:+.2f} ({pnl_pct_tot:+.1f}%)",
            f"💰 Balance:        ${cur_bal:.2f} / ${start_bal:.2f} start",
        ]

        if open_trades:
            lines.append("")
            lines.append(f"Open trades:      {len(open_trades)}")
            for t in open_trades[:10]:
                sym = t.get("symbol", "?")
                entry = t.get("actual_entry_price") or t.get("entry_price", 0)
                processed = t.get("processed_tps") or []
                last_tp = max((int(p.replace("tp", "")) for p in processed if p.startswith("tp")), default=0)
                tp_str = f"TP{last_tp} hit" if last_tp else "waiting TP5"
                lines.append(f"  {sym}  ({tp_str})")

        lines.append(f"{'━' * 28}")
        return self.send("\n".join(lines))

    # ── reversal warning format ──────────────────────────────────────

    def _fmt_reversal(self, d: dict) -> str:
        return (
            f"⚠️ <b>REVERSAL WARNING</b>\n"
            f"{'━' * 28}\n\n"
            f"📌 <b>{d['symbol']}</b>\n"
            f"💵 Entry:    {self._fp(d['entry_price'])}\n"
            f"🏔  Peak:     {self._fp(d['highest_price'])}  (+{d['high_pct']:.2f}%)\n"
            f"💵 Now:      {self._fp(d['current_price'])}  ({d['cur_pct']:+.2f}%)\n"
            f"📉 Drop:     {d['drop_pct']:.2f}% from peak\n"
            f"⏱  Age:      {d.get('age_str', '')}\n\n"
            f"Price has dropped significantly from its peak.\n"
            f"Consider taking remaining profits."
        )
