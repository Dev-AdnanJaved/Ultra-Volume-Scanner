"""""
Telegram command listener.

Commands:
  /report          — all active signals with performance
  /report SYMBOL   — detailed single-coin breakdown
  /summary         — win rate, averages, best/worst
  /active          — quick list of tracked symbols
  /detailed_report — sends JSON file of completed signals (≥7 days old) with all data
  /export_csv      — flat CSV of all signals (active + archived) for analysis
  /paper           — paper trading overview (account balance, open trades, stats)
  /paper SYMBOL    — detailed paper trade breakdown for one coin
  /help            — command reference
"""""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

from binance_client import BinanceClient
from tracker import SignalTracker

logger = logging.getLogger(__name__)

EXPORT_CHUNK_SIZE = 200


class TelegramCommandListener:

    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        tracker: SignalTracker,
        binance: BinanceClient,
        data_dir: str = "data",
        trader=None,
        strategy_mgr=None,
    ) -> None:
        self._token = bot_token
        self._chat_id = str(chat_id)
        self._tracker = tracker
        self._binance = binance
        self._trader = trader
        self._strategy_mgr = strategy_mgr
        self._session = requests.Session()
        self._offset: int = 0
        self._running = False
        self._data_dir = Path(data_dir)

    def _url(self, method: str) -> str:
        return self.API.format(token=self._token, method=method)

    def _send(self, chat_id: str, text: str) -> bool:
        MAX_LEN = 4000
        parts: list[str] = []
        while len(text) > MAX_LEN:
            idx = text.rfind("\n", 0, MAX_LEN)
            if idx == -1:
                idx = MAX_LEN
            parts.append(text[:idx])
            text = text[idx:].lstrip("\n")
        parts.append(text)

        for part in parts:
            if not part.strip():
                continue
            for attempt in range(3):
                try:
                    r = self._session.post(
                        self._url("sendMessage"),
                        json={
                            "chat_id": chat_id,
                            "text": part,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                        },
                        timeout=15,
                    ).json()
                    if r.get("ok"):
                        break
                    if r.get("error_code") == 429:
                        wait = r.get("parameters", {}).get("retry_after", 30)
                        time.sleep(wait)
                        continue
                    logger.error("Telegram send error: %s", r)
                    return False
                except Exception as exc:
                    logger.error("Telegram send failed (attempt %d): %s", attempt + 1, exc)
                    time.sleep(2)
            time.sleep(0.3)
        return True

    def _send_document(self, chat_id: str, file_path: str, caption: str = "") -> bool:
        """""Send a file as a Telegram document."""""
        for attempt in range(3):
            try:
                with open(file_path, "rb") as f:
                    r = self._session.post(
                        self._url("sendDocument"),
                        data={"chat_id": chat_id, "caption": caption},
                        files={"document": f},
                        timeout=30,
                    ).json()
                if r.get("ok"):
                    return True
                if r.get("error_code") == 429:
                    wait = r.get("parameters", {}).get("retry_after", 30)
                    time.sleep(wait)
                    continue
                logger.error("Telegram sendDocument error: %s", r)
                return False
            except Exception as exc:
                logger.error("Telegram sendDocument failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(2)
        return False

    @staticmethod
    def _chunks(lst: list, size: int = EXPORT_CHUNK_SIZE) -> list:
        return [lst[i:i + size] for i in range(0, len(lst), size)]

    def _send_chunked_json(self, chat_id: str, data: list, prefix: str, label: str) -> None:
        if not data:
            self._send(chat_id, f"📭 No signals for {label}.")
            return

        chunks = self._chunks(data)
        total_parts = len(chunks)
        now_ts = int(time.time())
        gen_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        for idx, chunk in enumerate(chunks, 1):
            part_label = f"Part {idx}/{total_parts} • " if total_parts > 1 else ""
            tmp_path = f"/tmp/{prefix}_part{idx}of{total_parts}_{now_ts}.json"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(chunk, f, indent=2)

                caption = (
                    f"{label}\n"
                    f"{part_label}{len(chunk)} signals\n"
                    f"Total: {len(data)}\n"
                    f"Generated: {gen_str}"
                )
                success = self._send_document(chat_id, tmp_path, caption)

                if not success:
                    self._send(chat_id, f"❌ Failed to send file part {idx}.")
                    return
            except Exception as exc:
                self._send(chat_id, f"❌ Failed to write/send file part {idx}: {exc}")
                return
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        logger.info("%s sent: %d signals in %d file(s)", label, len(data), total_parts)

    def _poll(self) -> list:
        try:
            resp = self._session.get(
                self._url("getUpdates"),
                params={
                    "offset": self._offset,
                    "timeout": 10,
                    "allowed_updates": '["message"]',
                },
                timeout=15,
            ).json()
            if not resp.get("ok"):
                return []
            return resp.get("result", [])
        except Exception:
            return []

    # ── paper trade file I/O ─────────────────────────────────────────

    def _load_paper_trades(self) -> list:
        path = self._data_dir / "trades.json"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                trades = json.load(f)
            return [t for t in trades if t.get("paper")]
        except Exception:
            return []

    def _load_paper_account(self) -> dict:
        path = self._data_dir / "paper_account.json"
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # ── formatting helpers ───────────────────────────────────────────

    @staticmethod
    def _fmt_price(price: float) -> str:
        if price <= 0:
            return "N/A"
        if price >= 1000:
            return f"${price:,.2f}"
        if price >= 1:
            return f"${price:.4f}"
        if price >= 0.001:
            return f"${price:.6f}"
        return f"${price:.8f}"

    @staticmethod
    def _fmt_pct(pct: float) -> str:
        icon = "🟢" if pct > 0 else "🔴" if pct < 0 else "⚪"
        return f"{icon} {pct:+.2f}%"

    @staticmethod
    def _fmt_age(ts: float) -> str:
        age = time.time() - ts
        if age < 3600:
            return f"{int(age / 60)}m"
        hours = int(age // 3600)
        mins = int((age % 3600) // 60)
        return f"{hours}h {mins}m"

    @staticmethod
    def _calc_pct(entry: float, current: float) -> float:
        if entry <= 0:
            return 0.0
        return ((current - entry) / entry) * 100.0

    @staticmethod
    def _result_emoji(pct: float) -> str:
        if pct >= 10:
            return "🚀"
        if pct >= 5:
            return "✅"
        if pct >= 0:
            return "🟢"
        if pct >= -5:
            return "🟡"
        return "🔴"

    # ── main loop ────────────────────────────────────────────────────

    def run(self) -> None:
        self._running = True
        logger.info("Telegram command listener started")
        updates = self._poll()
        if updates:
            self._offset = updates[-1]["update_id"] + 1
            logger.info("Skipped %d old queued messages", len(updates))
        while self._running:
            try:
                updates = self._poll()
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._handle(update)
            except Exception:
                logger.error("Command listener error", exc_info=True)
                time.sleep(5)

    def stop(self) -> None:
        self._running = False

    # ── dispatcher ───────────────────────────────────────────────────

    def _handle(self, update: dict) -> None:
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        if chat_id != self._chat_id:
            return
        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        logger.info("Command received: %s %s", cmd, args)

        handlers = {
            "/report":          lambda: self._cmd_report(chat_id, args),
            "/summary":         lambda: self._cmd_summary(chat_id),
            "/active":          lambda: self._cmd_active(chat_id),
            "/export":          lambda: self._cmd_export(chat_id),
            "/coin":            lambda: self._cmd_coin(chat_id, args),
            "/detailed_report": lambda: self._cmd_detailed_report(chat_id),
            "/export_csv":      lambda: self._cmd_export_csv(chat_id),
            "/paper":           lambda: self._cmd_paper(chat_id, args),
            "/validate":        lambda: self._cmd_validate(chat_id),
            "/slstatus":        lambda: self._cmd_slstatus(chat_id),
            "/dumpalert":       lambda: self._cmd_dumpalert(chat_id),
            # ── testnet / live-path test commands ─────────────────────────
            "/testopen":        lambda: self._cmd_testopen(chat_id, args),
            "/testtp":          lambda: self._cmd_testtp(chat_id, args),
            "/testsl":          lambda: self._cmd_testsl(chat_id, args),
            "/testdump":        lambda: self._cmd_testdump(chat_id),
            "/testemerge":      lambda: self._cmd_testemerge(chat_id, args),
            "/testtl":          lambda: self._cmd_testtl(chat_id, args),
            "/testclose":       lambda: self._cmd_testclose(chat_id, args),
            "/testall":         lambda: self._cmd_testall(chat_id),
            "/testliveorder":   lambda: self._cmd_testliveorder(chat_id, args),
            "/help":            lambda: self._cmd_help(chat_id),
            "/start":           lambda: self._cmd_help(chat_id),
        }
        handler = handlers.get(cmd)
        if handler:
            handler()
        else:
            self._send(chat_id, "❓ Unknown command. Send /help")

    # ── /report ──────────────────────────────────────────────────────

    def _cmd_report(self, chat_id: str, args: list) -> None:
        try:
            prices = self._binance.get_mark_prices()
            self._tracker.apply_prices(prices)
        except Exception:
            prices = {}

        signals = self._tracker.get_active_signals()
        if not signals:
            self._send(chat_id, "📊 No active signals.")
            return

        if args:
            sym = args[0].upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            matches = [s for s in signals if s["symbol"] == sym]
            if not matches:
                self._send(chat_id, f"📊 No active signal for <b>{sym}</b>")
                return
            for sig in matches:
                self._send_detailed_report(chat_id, sig, prices)
            return

        signals.sort(key=lambda s: s["alert_time_ts"], reverse=True)
        lines = ["📊 <b>PERFORMANCE REPORT</b>", ""]

        valid_changes: list[float] = []
        valid_highest: list[float] = []

        for sig in signals:
            sym = sig["symbol"]
            entry = sig.get("entry_price", 0)
            highest = sig.get("highest_price", entry)
            current = prices.get(sym, sig.get("current_price", 0))
            if current > highest:
                highest = current

            age = self._fmt_age(sig["alert_time_ts"])
            brk = sig.get("breakout_margin_pct", 0)

            if entry > 0 and current > 0:
                cur_pct = self._calc_pct(entry, current)
                high_pct = self._calc_pct(entry, highest)
                valid_changes.append(cur_pct)
                valid_highest.append(high_pct)
                emoji = self._result_emoji(cur_pct)
                lines.append(f"{emoji} <b>{sym}</b>  •  {age}")
                lines.append(f"   Now: {cur_pct:+.2f}%  │  Peak: {high_pct:+.2f}%  │  Brk: +{brk:.2f}%")
                lines.append("")
            else:
                lines.append(f"⚪ <b>{sym}</b>  •  {age}  •  No price data")
                lines.append("")

        if valid_changes:
            total = len(valid_changes)
            avg_cur = sum(valid_changes) / total
            avg_high = sum(valid_highest) / total
            winners = sum(1 for c in valid_changes if c > 0)
            peak_w = sum(1 for h in valid_highest if h > 2)
            lines.append("━" * 26)
            lines.append(f"📡 Signals:    {total}")
            lines.append(f"📊 Avg now:    {avg_cur:+.2f}%")
            lines.append(f"🏔  Avg peak:   {avg_high:+.2f}%")
            lines.append(f"🎯 Win now:    {winners}/{total} ({winners/total*100:.0f}%)")
            lines.append(f"🎯 Win peak:   {peak_w}/{total} ({peak_w/total*100:.0f}%)")
            lines.append("")
            lines.append("━━━ 🎯 TP HITS ━━━")
            for tp in self._tracker.tp_targets:
                count = sum(
                    1 for s in signals
                    if s.get("outcome", {}).get(f"tp{tp}_hit", False)
                )
                label = f"{count} signal{'s' if count != 1 else ''}" if count > 0 else "0"
                lines.append(f"TP +{tp}%:".ljust(11) + label)
            lines.append("")
            lines.append("💡 /report SYMBOL for details")

        self._send(chat_id, "\n".join(lines))

    def _send_detailed_report(self, chat_id: str, sig: dict, prices: dict) -> None:
        sym = sig["symbol"]
        entry = sig.get("entry_price", 0)
        highest = sig.get("highest_price", entry)
        lowest = sig.get("lowest_price", entry)
        current = prices.get(sym, sig.get("current_price", 0))
        if current > highest:
            highest = current

        cur_pct = self._calc_pct(entry, current) if entry > 0 else 0
        high_pct = self._calc_pct(entry, highest) if entry > 0 else 0
        low_pct = self._calc_pct(entry, lowest) if entry > 0 and lowest > 0 else 0
        age = self._fmt_age(sig["alert_time_ts"])

        lines = [
            f"📊 <b>{sym} — DETAILED</b>",
            "",
            "━━━ 💵 PRICE ━━━",
            f"Entry:     {self._fmt_price(entry)}",
            f"Current:   {self._fmt_price(current)}   {self._fmt_pct(cur_pct)}",
            f"Peak:      {self._fmt_price(highest)}   {self._fmt_pct(high_pct)}",
            f"Lowest:    {self._fmt_price(lowest)}   {self._fmt_pct(low_pct)}",
            f"Age:       {age}",
            "",
            "━━━ 🔺 MAIN CRITERIA ━━━",
            f"Breakout:  +{sig.get('breakout_margin_pct', 0):.2f}% above 24h high",
            f"Volume:    {sig.get('vol_candle_1_fmt','?')} → {sig.get('vol_candle_2_fmt','?')} → {sig.get('vol_candle_3_fmt','?')}",
            f"24h chg:   {sig.get('price_change_24h', 0):+.1f}%",
        ]

        add = sig.get("additional_data", {})
        if add:
            lines.append("")
            lines.append("━━━ 📈 ADDITIONAL DATA ━━━")
            if add.get("rvol_20") is not None:
                lines.append(f"RVOL (20):  {add['rvol_20']:.2f}x")
            if add.get("oi_change_pct") is not None:
                lines.append(f"OI change:  {add['oi_change_pct']:+.2f}%")
            if add.get("funding_rate") is not None:
                fr = add["funding_rate"]
                fr_ok = "✅" if add.get("funding_in_ideal_range") else "⚠️"
                lines.append(f"Funding:    {fr_ok} {fr:.4f}%")
            if add.get("vol_24h_usdt") is not None:
                vol_m = add["vol_24h_usdt"] / 1e6
                liq_ok = "✅" if add.get("vol_24h_above_50m") else "⚠️"
                lines.append(f"24h Vol:    {liq_ok} ${vol_m:.1f}M")
            if add.get("price_above_ema50_4h") is not None:
                ema_ok = "✅ above" if add["price_above_ema50_4h"] else "⚠️ below"
                lines.append(f"4h EMA50:   {ema_ok} ({add.get('ema50_distance_pct', 0):+.2f}%)")
            if add.get("volatility_compression_ratio") is not None:
                cr = add["volatility_compression_ratio"]
                comp = "✅ compressed" if add.get("is_compressed") else "➡️ normal"
                lines.append(f"Volatility: {comp} (ratio {cr:.2f})")

        outcome = sig.get("outcome", {})
        if outcome:
            lines.append("")
            lines.append("━━━ 📉 OUTCOME ━━━")
            sig_type = outcome.get("signal_type", "active")
            type_icons = {"fast": "⚡", "slow": "🐌", "delayed": "🕐", "failed": "❌", "active": "🔄"}
            lines.append(f"Type:      {type_icons.get(sig_type, '❓')} {sig_type}")

            closed = outcome.get("signal_closed", False)
            close_reason = outcome.get("close_reason")
            lines.append(f"Status:    {'🔒 Closed' if closed else '🟢 Active'}" + (f" ({close_reason})" if close_reason else ""))

            dd = outcome.get("max_drawdown_pct", 0)
            dd_hrs = outcome.get("max_drawdown_hours_after_entry")
            dd_time = f" ({dd_hrs:.1f}h after entry)" if dd_hrs is not None else ""
            lines.append(f"Max DD:    {dd:+.2f}%{dd_time}")

            neg = outcome.get("went_negative_before_tp", False)
            neg_hrs = outcome.get("hours_negative_total", 0)
            lines.append(f"Neg b/TP:  {'Yes' if neg else 'No'}" + (f" ({neg_hrs:.1f}h total)" if neg_hrs > 0 else ""))

            peak_hrs = outcome.get("peak_hours_after_entry")
            if peak_hrs is not None:
                lines.append(f"Peak at:   {peak_hrs:.1f}h after entry")

            first_tp_hrs = None
            for tp in self._tracker.tp_targets:
                key = f"tp{tp}"
                if outcome.get(f"{key}_hit"):
                    tp_hrs = outcome.get(f"{key}_hit_hours_after_entry")
                    tp_dd = outcome.get(f"{key}_max_drawdown_before", 0)
                    tp_line = f"TP +{tp}%:   ✅ hit"
                    if tp_hrs is not None:
                        tp_line += f" @ {tp_hrs:.1f}h"
                        if first_tp_hrs is None or tp_hrs < first_tp_hrs:
                            first_tp_hrs = tp_hrs
                    if tp_dd and tp_dd < 0:
                        tp_line += f" (DD before: {tp_dd:+.2f}%)"
                    lines.append(tp_line)

                    snap = sig.get(f"{key}_snapshot")
                    if snap:
                        snap_parts = []
                        oi_chg = snap.get("oi_change_pct")
                        if oi_chg is not None:
                            snap_parts.append(f"OI {oi_chg:+.1f}%")
                        fr = snap.get("funding_rate")
                        if fr is not None:
                            fr_ok = "✅" if snap.get("funding_in_ideal_range") else "⚠️"
                            snap_parts.append(f"FR {fr_ok}{fr:.4f}%")
                        mom_1h = snap.get("price_momentum_1h_pct")
                        if mom_1h is not None:
                            snap_parts.append(f"1h {mom_1h:+.1f}%")
                        mom_4h = snap.get("price_momentum_4h_pct")
                        if mom_4h is not None:
                            snap_parts.append(f"4h {mom_4h:+.1f}%")
                        colors = snap.get("candle_colors_at_hit")
                        if colors:
                            color_str = "".join("🟢" if c == "green" else "🔴" for c in colors)
                            snap_parts.append(color_str)
                        if snap_parts:
                            lines.append(f"         📸 {' | '.join(snap_parts)}")

            if first_tp_hrs is not None:
                lines.append(f"1st TP:    ⚡ {first_tp_hrs:.1f}h after entry")

            btc_to_tp = outcome.get("btc_change_entry_to_tp")
            if btc_to_tp is not None:
                lines.append(f"BTC→1stTP: {btc_to_tp:+.2f}%")

            btc_trend = outcome.get("btc_trend_during_signal")
            if btc_trend:
                trend_icons = {"pumping": "🟢", "dumping": "🔴", "ranging": "➡️"}
                lines.append(f"BTC trend: {trend_icons.get(btc_trend, '❓')} {btc_trend}")

        btc_at = sig.get("btc_price")
        btc_now = prices.get("BTCUSDT")
        if btc_at and btc_now:
            btc_chg = self._calc_pct(btc_at, btc_now)
            lines.append("")
            lines.append("━━━ ₿ BTC CONTEXT ━━━")
            lines.append(f"BTC:  {self._fmt_price(btc_at)} → {self._fmt_price(btc_now)}  ({btc_chg:+.2f}%)")

        lines.append("")
        lines.append(f"🕐 Signal: {sig.get('alert_time', 'N/A')}")

        self._send(chat_id, "\n".join(lines))

    # ── /summary ─────────────────────────────────────────────────────

    @staticmethod
    def _summary_group_stats(sigs: list, prices: dict) -> dict:
        """""Compute stats for a group of signals."""""
        valid = [s for s in sigs if s.get("entry_price", 0) > 0]
        if not valid:
            return {}
        changes, peaks = [], []
        for s in valid:
            entry = s["entry_price"]
            cur = prices.get(s["symbol"], s.get("current_price", entry))
            high = max(s.get("highest_price", entry), cur)
            changes.append(((cur - entry) / entry) * 100)
            peaks.append(((high - entry) / entry) * 100)
        return {
            "count":    len(valid),
            "avg_now":  sum(changes) / len(changes),
            "avg_peak": sum(peaks) / len(peaks),
            "win_now":  sum(1 for c in changes if c > 0),
            "win_peak": sum(1 for h in peaks if h > 2),
            "best":     (valid[changes.index(max(changes))]["symbol"], max(changes)),
            "worst":    (valid[changes.index(min(changes))]["symbol"], min(changes)),
        }

    @staticmethod
    def _history_group_stats(sigs: list, tp_targets: list) -> dict:
        """""Compute TP hit rates and win stats for archived signals."""""
        valid = [s for s in sigs if s.get("exit_pct") is not None]
        if not valid:
            return {}
        exits = [s["exit_pct"] for s in valid]
        peaks = [s.get("peak_pct") or s.get("highest_pct") or 0 for s in valid]
        tp_rates = {}
        for tp in tp_targets:
            hit = sum(1 for s in valid if s.get("outcome", {}).get(f"tp{tp}_hit", False))
            tp_rates[tp] = (hit, len(valid))
        return {
            "count":    len(valid),
            "avg_exit": sum(exits) / len(exits),
            "avg_peak": sum(peaks) / len(peaks),
            "win_rate": sum(1 for e in exits if e > 0),
            "tp_rates": tp_rates,
        }

    def _fmt_group_active(self, label: str, icon: str, stats: dict) -> list[str]:
        if not stats:
            return [f"{icon} <b>{label}</b>  —  no signals", ""]
        n = stats["count"]
        lines = [f"{icon} <b>{label}</b>  ({n} signal{'s' if n != 1 else ''})"]
        lines.append(f"  Avg now:   {stats['avg_now']:+.2f}%")
        lines.append(f"  Avg peak:  {stats['avg_peak']:+.2f}%")
        lines.append(f"  Win now:   {stats['win_now']}/{n} ({stats['win_now']/n*100:.0f}%)")
        lines.append(f"  Win peak:  {stats['win_peak']}/{n} ({stats['win_peak']/n*100:.0f}%)")
        lines.append(f"  🚀 Best:   {stats['best'][0]} {stats['best'][1]:+.2f}%")
        lines.append(f"  🔴 Worst:  {stats['worst'][0]} {stats['worst'][1]:+.2f}%")
        lines.append("")
        return lines

    def _fmt_group_history(self, label: str, icon: str, stats: dict) -> list[str]:
        if not stats:
            return [f"{icon} <b>{label}</b>  —  no history", ""]
        n = stats["count"]
        wr = stats["win_rate"]
        lines = [f"{icon} <b>{label}</b>  ({n} completed)"]
        lines.append(f"  Avg exit:  {stats['avg_exit']:+.2f}%")
        lines.append(f"  Avg peak:  {stats['avg_peak']:+.2f}%")
        lines.append(f"  Win rate:  {wr}/{n} ({wr/n*100:.0f}%)")
        tp_parts = []
        for tp, (hit, total) in stats["tp_rates"].items():
            if hit > 0:
                tp_parts.append(f"TP{tp}: {hit}/{total} ({hit/total*100:.0f}%)")
        if tp_parts:
            lines.append(f"  {' │ '.join(tp_parts)}")
        lines.append("")
        return lines

    def _cmd_summary(self, chat_id: str) -> None:
        try:
            prices = self._binance.get_mark_prices()
            self._tracker.apply_prices(prices)
        except Exception:
            prices = {}

        signals = self._tracker.get_active_signals()
        history = self._tracker.get_history()
        tp_targets = self._tracker.tp_targets

        premium_active  = [s for s in signals if s.get("premium", False)]
        standard_active = [s for s in signals if not s.get("premium", False)]
        premium_hist    = [s for s in history if s.get("premium", False)]
        standard_hist   = [s for s in history if not s.get("premium", False)]

        lines = ["📊 <b>SUMMARY — Premium vs Standard</b>", ""]

        # ── Active signals ──────────────────────────────────────────
        total_active = len([s for s in signals if s.get("entry_price", 0) > 0])
        lines.append(f"━━━ 📡 ACTIVE ({total_active} total) ━━━")
        lines.append("")
        lines.extend(self._fmt_group_active(
            "💎 PREMIUM (vol ≥$20M)", "💎",
            self._summary_group_stats(premium_active, prices),
        ))
        lines.extend(self._fmt_group_active(
            "🚨 STANDARD (vol &lt;$20M)", "🚨",
            self._summary_group_stats(standard_active, prices),
        ))

        # ── History ─────────────────────────────────────────────────
        lines.append(f"━━━ 📜 HISTORY ({len(history)} completed) ━━━")
        lines.append("")
        lines.extend(self._fmt_group_history(
            "💎 PREMIUM", "💎",
            self._history_group_stats(premium_hist, tp_targets),
        ))
        lines.extend(self._fmt_group_history(
            "🚨 STANDARD (vol &lt;$20M)", "🚨",
            self._history_group_stats(standard_hist, tp_targets),
        ))

        self._send(chat_id, "\n".join(lines))

    # ── /active ──────────────────────────────────────────────────────

    def _cmd_active(self, chat_id: str) -> None:
        signals = self._tracker.get_active_signals()
        if not signals:
            self._send(chat_id, "📡 No active signals.")
            return

        signals.sort(key=lambda s: s["alert_time_ts"], reverse=True)
        lines = [f"📡 <b>ACTIVE ({len(signals)})</b>", ""]

        for sig in signals:
            age = self._fmt_age(sig["alert_time_ts"])
            sym = sig["symbol"]
            brk = sig.get("breakout_margin_pct", 0)
            v1 = sig.get("vol_candle_1_fmt", "?")
            v3 = sig.get("vol_candle_3_fmt", "?")
            lines.append(f"• <b>{sym}</b>  {age}  brk:+{brk:.1f}%  vol:{v1}→{v3}")

        lines.append("")
        lines.append(f"Window: {self._tracker.max_age_hours}h")
        lines.append("/report SYMBOL for details")
        self._send(chat_id, "\n".join(lines))

    # ── /detailed_report ─────────────────────────────────────────────

    def _cmd_detailed_report(self, chat_id: str) -> None:
        self._send(chat_id, "⏳ Building detailed report, please wait…")

        try:
            completed = self._tracker.get_completed_signals(
                self._tracker.detailed_report_min_age_seconds
            )
        except Exception as exc:
            self._send(chat_id, f"❌ Error loading signals: {exc}")
            return

        if not completed:
            min_h = int(self._tracker.detailed_report_min_age_seconds // 3600)
            self._send(
                chat_id,
                f"📭 No completed signals yet.\n"
                f"Signals need to be at least {min_h}h old to appear in this report.\n"
                f"Use /report or /summary to see active signals."
            )
            return

        report = []
        for sig in completed:
            entry = sig.get("entry_price", 0)
            highest = sig.get("highest_price", 0)
            lowest = sig.get("lowest_price", 0)
            current = sig.get("current_price", 0)

            record = {
                "symbol":              sig.get("symbol"),
                "timeframe":           sig.get("timeframe", "1h"),
                "signal_time":         sig.get("alert_time"),
                "archived_time":       sig.get("archived_time"),
                "tracked_hours":       sig.get("tracked_hours"),
                "entry_price":         entry,
                "exit_price":          sig.get("exit_price", current),
                "peak_price":          highest,
                "lowest_price":        lowest,
                "peak_pct":            sig.get("peak_pct"),
                "lowest_pct":          sig.get("lowest_pct"),
                "exit_pct":            sig.get("exit_pct"),
                "tp_targets_hit":      sig.get("tp_sent", []),
                "reversal_warned":     sig.get("reversal_warned", False),
                "main_criteria": {
                    "breakout_margin_pct": sig.get("breakout_margin_pct"),
                    "high_24h_at_signal":  sig.get("high_24h"),
                    "vol_candle_1":        sig.get("vol_candle_1"),
                    "vol_candle_2":        sig.get("vol_candle_2"),
                    "vol_candle_3":        sig.get("vol_candle_3"),
                    "vol_candle_1_fmt":    sig.get("vol_candle_1_fmt"),
                    "vol_candle_2_fmt":    sig.get("vol_candle_2_fmt"),
                    "vol_candle_3_fmt":    sig.get("vol_candle_3_fmt"),
                    "rvol":                sig.get("rvol"),
                    "price_change_24h":    sig.get("price_change_24h"),
                },
                "additional_data":     sig.get("additional_data", {}),
                "btc_price_at_signal": sig.get("btc_price"),
                "candle_time":         sig.get("candle_time"),
                "high_breakout_warning": sig.get("high_breakout_warning", False),
                "outcome":             sig.get("outcome", {}),
                "price_journey":       sig.get("price_journey", []),
            }
            for k, v in sig.items():
                if k.endswith("_snapshot") and k.startswith("tp") and isinstance(v, dict):
                    record[k] = v
            report.append(record)

        chunks = self._chunks(report)
        if len(chunks) > 1:
            self._send(chat_id, f"📊 {len(report)} signals → {len(chunks)} files ({EXPORT_CHUNK_SIZE} per file)")

        self._send_chunked_json(chat_id, report, "detailed_report", "📊 Detailed Signal Report")

    # ── /export ─────────────────────────────────────────────────────

    def _cmd_export(self, chat_id: str) -> None:
        self._send(chat_id, "⏳ Exporting active signals…")

        try:
            prices = self._binance.get_mark_prices()
            self._tracker.apply_prices(prices)
        except Exception:
            prices = {}

        signals = self._tracker.get_active_signals()
        if not signals:
            self._send(chat_id, "📭 No active signals to export.")
            return

        for sig in signals:
            sym = sig["symbol"]
            entry = sig.get("entry_price", 0)
            current = prices.get(sym, sig.get("current_price", 0))
            highest = sig.get("highest_price", entry)
            lowest = sig.get("lowest_price", entry)
            if current > highest:
                highest = current
            sig["current_price"] = current
            sig["highest_price"] = highest
            if entry > 0:
                sig["current_pct"] = round(((current - entry) / entry) * 100, 2)
                sig["peak_pct"] = round(((highest - entry) / entry) * 100, 2)
                sig["lowest_pct"] = round(((lowest - entry) / entry) * 100, 2) if lowest > 0 else None
            sig.pop("_prev_highest", None)
            sig.pop("_prev_lowest", None)

        chunks = self._chunks(signals)
        if len(chunks) > 1:
            self._send(chat_id, f"📡 {len(signals)} signals → {len(chunks)} files ({EXPORT_CHUNK_SIZE} per file)")

        self._send_chunked_json(chat_id, signals, "active_signals", "📡 Active Signals Export")

    # ── /coin ──────────────────────────────────────────────────────────

    def _cmd_coin(self, chat_id: str, args: list) -> None:
        if not args:
            self._send(chat_id, "Usage: /coin ETH  or  /coin ETHUSDT")
            return

        sym = args[0].upper()
        if not sym.endswith("USDT"):
            sym += "USDT"

        try:
            prices = self._binance.get_mark_prices()
            self._tracker.apply_prices(prices)
        except Exception:
            prices = {}

        signals = self._tracker.get_active_signals()
        matches = [s for s in signals if s["symbol"] == sym]

        if not matches:
            self._send(chat_id, f"📭 No active signal for <b>{sym}</b>")
            return

        for sig in matches:
            entry = sig.get("entry_price", 0)
            current = prices.get(sym, sig.get("current_price", 0))
            highest = sig.get("highest_price", entry)
            lowest = sig.get("lowest_price", entry)
            if current > highest:
                highest = current
            sig["current_price"] = current
            sig["highest_price"] = highest
            if entry > 0:
                sig["current_pct"] = round(((current - entry) / entry) * 100, 2)
                sig["peak_pct"] = round(((highest - entry) / entry) * 100, 2)
                sig["lowest_pct"] = round(((lowest - entry) / entry) * 100, 2) if lowest > 0 else None
            sig.pop("_prev_highest", None)
            sig.pop("_prev_lowest", None)

        data = matches

        now_ts = int(time.time())
        tmp_path = f"/tmp/{sym}_{now_ts}.json"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self._send(chat_id, f"❌ Failed to write file: {exc}")
            return

        caption = (
            f"📌 {sym} Signal Export\n"
            f"Signals: {len(matches)}\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        success = self._send_document(chat_id, tmp_path, caption)

        try:
            os.remove(tmp_path)
        except Exception:
            pass

        if not success:
            self._send(chat_id, "❌ Failed to send file. Check bot logs.")
        else:
            logger.info("Coin export sent for %s: %d signal(s)", sym, len(matches))

    # ── /export_csv ──────────────────────────────────────────────────

    def _cmd_export_csv(self, chat_id: str) -> None:
        self._send(chat_id, "⏳ Building flat CSV export…")

        try:
            from export_csv import load_all_signals, build_csv, compute_fieldnames

            signals = load_all_signals(active=True, history=True)
            if not signals:
                self._send(chat_id, "📭 No signals found (active or archived).")
                return

            all_fieldnames = compute_fieldnames(signals)

            chunks = self._chunks(signals)
            total_parts = len(chunks)
            if total_parts > 1:
                self._send(chat_id, f"📊 {len(signals)} signals → {total_parts} files ({EXPORT_CHUNK_SIZE} per file)")

            now_ts = int(time.time())
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            gen_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            for idx, chunk in enumerate(chunks, 1):
                part_label = f"Part {idx}/{total_parts} • " if total_parts > 1 else ""
                tmp_path = f"/tmp/signals_flat_part{idx}of{total_parts}_{now_str}_{now_ts}.csv"
                try:
                    count = build_csv(chunk, tmp_path, fieldnames=all_fieldnames)

                    file_size = os.path.getsize(tmp_path)
                    size_str = f"{file_size / 1024:.1f} KB" if file_size < 1_048_576 else f"{file_size / 1_048_576:.1f} MB"

                    caption = (
                        f"📊 Flat CSV Export\n"
                        f"{part_label}{count} signals\n"
                        f"Total: {len(signals)}\n"
                        f"Size: {size_str}\n"
                        f"Generated: {gen_str}"
                    )
                    success = self._send_document(chat_id, tmp_path, caption)

                    if not success:
                        self._send(chat_id, f"❌ Failed to send CSV file part {idx}.")
                        return
                except Exception as exc:
                    logger.error("CSV export chunk %d failed: %s", idx, exc)
                    self._send(chat_id, f"❌ CSV export failed on part {idx}: {exc}")
                    return
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

            logger.info("CSV export sent: %d signals in %d file(s)", len(signals), total_parts)
        except Exception as exc:
            logger.error("CSV export failed: %s", exc)
            self._send(chat_id, f"❌ CSV export failed: {exc}")

    # ── /paper ───────────────────────────────────────────────────────

    def _cmd_paper(self, chat_id: str, args: list) -> None:
        trades  = self._load_paper_trades()
        account = self._load_paper_account()

        if not trades and not account:
            self._send(
                chat_id,
                "📝 <b>PAPER TRADING</b>\n\n"
                "No paper trades found.\n"
                "Enable paper mode in config.json:\n"
                "<code>trading.enabled = true\n"
                "trading.paper_mode = true</code>"
            )
            return

        # ── /paper SYMBOL — single trade detail ───────────────────────
        if args:
            sym = args[0].upper()
            if not sym.endswith("USDT"):
                sym += "USDT"
            matches = [t for t in trades if t.get("symbol") == sym]
            if not matches:
                self._send(chat_id, f"📝 No paper trade found for <b>{sym}</b>")
                return
            # Show most recent trade for that symbol
            matches.sort(key=lambda t: t.get("opened_ts", 0), reverse=True)
            self._send_paper_trade_detail(chat_id, matches[0])
            return

        # ── /paper — overview ─────────────────────────────────────────
        try:
            prices = self._binance.get_mark_prices()
        except Exception:
            prices = {}

        open_trades   = [t for t in trades if t.get("status") == "open"]
        closed_trades = [t for t in trades if t.get("status", "").startswith("closed")]

        lines = ["📝 <b>PAPER TRADING</b>", ""]

        # ── Account block ─────────────────────────────────────────────
        if account:
            start_bal = account.get("starting_balance", 0)
            cur_bal   = account.get("current_balance",  0)
            total_pnl = account.get("total_realized_pnl", 0.0)
            opened    = account.get("trades_opened", len(trades))
            closed_n  = account.get("trades_closed", len(closed_trades))
            pnl_pct   = (total_pnl / start_bal * 100) if start_bal else 0
            pnl_icon  = "📈" if total_pnl >= 0 else "📉"

            lines.append("━━━ 💰 ACCOUNT ━━━")
            lines.append(f"Balance:    ${cur_bal:,.2f} / ${start_bal:,.2f} start")
            lines.append(f"{pnl_icon} Total PnL:  ${total_pnl:+,.2f} ({pnl_pct:+.2f}%)")
            lines.append(f"Opened:     {opened}  ·  Closed: {closed_n}")
            lines.append("")

        # ── Open trades ───────────────────────────────────────────────
        lines.append(f"━━━ 🟢 OPEN ({len(open_trades)}) ━━━")
        if not open_trades:
            lines.append("No open paper trades.")
        else:
            open_trades.sort(key=lambda t: t.get("opened_ts", 0), reverse=True)
            for t in open_trades:
                sym      = t["symbol"]
                entry    = t.get("actual_entry_price") or t.get("entry_price", 0)
                sl       = t.get("sl_price", 0)
                sl_type  = t.get("sl_type", "fixed")
                current  = prices.get(sym, 0)
                age      = self._fmt_age(t.get("opened_ts", time.time()))
                pnl_pct  = self._calc_pct(entry, current) if (entry > 0 and current > 0) else None
                processed = [p.replace("tp", "TP") for p in (t.get("processed_tps") or [])]
                tp_str   = ("TP" + " ".join(p[2:] for p in processed)) if processed else "waiting TP5"
                remaining_pct = (
                    round(t.get("remaining_quantity", 0) / t.get("quantity", 1) * 100)
                    if t.get("quantity", 0) > 0 else 100
                )

                pnl_str  = f"  {pnl_pct:+.1f}%" if pnl_pct is not None else ""
                price_str = self._fmt_price(current) if current else "no price"
                sl_str   = f"sl {self._fmt_price(sl)}"
                if "trailing" in sl_type:
                    sl_str += " 🔄"
                lines.append(
                    f"• <b>{sym}</b>  {age}  in:{self._fmt_price(entry)} "
                    f"now:{price_str}{pnl_str}"
                )
                lines.append(
                    f"   {sl_str}  rem:{remaining_pct}%  [{tp_str}]"
                )
                if t.get("time_limit_expires_str"):
                    lines.append(f"   ⏱ expires: {t['time_limit_expires_str']}")
        lines.append("")

        # ── Closed trades stats ───────────────────────────────────────
        lines.append(f"━━━ 📊 CLOSED ({len(closed_trades)}) ━━━")
        if not closed_trades:
            lines.append("No closed paper trades yet.")
        else:
            tp_targets = self._tracker.tp_targets
            wins     = [t for t in closed_trades if (t.get("pnl_pct") or 0) > 0]
            losses   = [t for t in closed_trades if (t.get("pnl_pct") or 0) <= 0]
            sl_hits  = [t for t in closed_trades if t.get("close_reason") == "sl_hit"]
            tl_hits  = [t for t in closed_trades if "time_limit" in (t.get("close_reason") or "")]
            tp_exits = [t for t in closed_trades if "tp" in (t.get("close_reason") or "")]

            total_n    = len(closed_trades)
            win_rate   = len(wins) / total_n * 100 if total_n else 0
            avg_pnl    = sum(t.get("pnl_pct") or 0 for t in closed_trades) / total_n if total_n else 0
            avg_pnl_w  = sum(t.get("pnl_pct") or 0 for t in wins) / len(wins) if wins else 0
            avg_pnl_l  = sum(t.get("pnl_pct") or 0 for t in losses) / len(losses) if losses else 0

            lines.append(f"Win rate:   {len(wins)}/{total_n} ({win_rate:.0f}%)")
            lines.append(f"Avg PnL:    {avg_pnl:+.2f}%")
            if wins:
                lines.append(f"Avg win:    {avg_pnl_w:+.2f}%  ·  Avg loss: {avg_pnl_l:+.2f}%")
            lines.append(f"SL hits:    {len(sl_hits)}/{total_n} ({len(sl_hits)/total_n*100:.0f}%)")
            lines.append(f"Time limits:{len(tl_hits)}/{total_n}")
            lines.append(f"TP exits:   {len(tp_exits)}/{total_n}")
            lines.append("")

            lines.append("━━━ 🎯 TP HIT RATES ━━━")
            for tp in tp_targets:
                key = f"tp{tp}"
                count = sum(
                    1 for t in closed_trades
                    if key in (t.get("processed_tps") or [])
                )
                bar = "█" * count + "░" * (total_n - count)
                pct = count / total_n * 100 if total_n else 0
                lines.append(f"TP{tp:>3}%:  {count}/{total_n} ({pct:.0f}%)  {bar}")

        lines.append("")
        lines.append("💡 /paper SYMBOL for trade details")
        self._send(chat_id, "\n".join(lines))

    def _send_paper_trade_detail(self, chat_id: str, trade: dict) -> None:
        sym      = trade["symbol"]
        entry    = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        sl       = trade.get("sl_price", 0)
        sl_type  = trade.get("sl_type", "fixed")
        sl_pct_v = trade.get("sl_pct", 0)
        lev      = trade.get("leverage", 1)
        margin   = trade.get("margin_used", 0)
        qty      = trade.get("quantity", 0)
        rem_qty  = trade.get("remaining_quantity", qty)
        rem_pct  = round(rem_qty / qty * 100) if qty > 0 else 0
        status   = trade.get("status", "open")
        is_open  = status == "open"
        age      = self._fmt_age(trade.get("opened_ts", time.time()))
        processed = trade.get("processed_tps") or []
        tp_decisions = trade.get("tp_decisions") or {}

        try:
            prices  = self._binance.get_mark_prices()
            current = prices.get(sym, 0)
        except Exception:
            current = 0

        cur_pct = self._calc_pct(entry, current) if (entry > 0 and current > 0 and is_open) else None
        pnl_icon = "📈" if (trade.get("pnl_pct") or cur_pct or 0) >= 0 else "📉"

        lines = [
            f"📝 <b>PAPER TRADE — {sym}</b>",
            "",
            "━━━ 💵 PRICES ━━━",
            f"Entry:      {self._fmt_price(entry)}",
        ]

        if is_open and current > 0:
            lines.append(f"Current:    {self._fmt_price(current)}  ({cur_pct:+.2f}%)")
        elif trade.get("close_price"):
            close_p   = trade["close_price"]
            close_pct = self._calc_pct(entry, close_p) if entry > 0 else 0
            lines.append(f"Closed at:  {self._fmt_price(close_p)}  ({close_pct:+.2f}%)")

        sl_type_label = f"trailing {sl_type.split('_')[1]}%" if "trailing" in sl_type else "fixed"
        lines.append(f"SL:         {self._fmt_price(sl)}  (-{sl_pct_v:.1f}% initial, {sl_type_label})")
        lines.append("")

        lines.append("━━━ 📦 POSITION ━━━")
        lines.append(f"Status:     {'🟢 Open' if is_open else '🔴 ' + (trade.get('close_reason') or status).replace('_', ' ').title()}")
        lines.append(f"Leverage:   {lev}x  ·  Margin: ${margin:.2f}")
        lines.append(f"Qty:        {qty:g}  ·  Remaining: {rem_qty:g} ({rem_pct}%)")
        lines.append(f"Age:        {age}")
        lines.append(f"Opened:     {trade.get('opened_at', '—')}")
        if trade.get("closed_at"):
            lines.append(f"Closed:     {trade.get('closed_at')}")

        if trade.get("pnl_pct") is not None:
            lines.append(f"{pnl_icon} Final PnL:  {trade['pnl_pct']:+.2f}%  (${trade.get('pnl_usdt', 0):+.2f})")

        # ── TP ladder ─────────────────────────────────────────────────
        tp_targets = self._tracker.tp_targets
        lines.append("")
        lines.append("━━━ 🎯 TP LADDER ━━━")
        for tp in tp_targets:
            key = f"tp{tp}"
            hit = key in processed
            dec = tp_decisions.get(key)

            if hit and dec:
                score     = dec.get("score", "?")
                close_pct = dec.get("close_pct", 0)
                fill_p    = dec.get("close_price", 0)
                tp_h      = dec.get("tp_hours", 0)
                pnl_u     = dec.get("partial_pnl_usdt", 0)
                sl_moved  = dec.get("sl_changed", False)
                new_sl    = dec.get("new_sl")

                hit_str = f"✅ TP{tp}%"
                if tp_h:
                    hit_str += f"  in {tp_h:.1f}h"
                if score != "?":
                    hit_str += f"  score {score}/10"
                lines.append(hit_str)

                action_str = dec.get("action_str", "")
                if action_str:
                    lines.append(f"   Action:  {action_str}")
                if close_pct > 0 and fill_p:
                    lines.append(
                        f"   Closed {close_pct}% at {self._fmt_price(fill_p)}"
                        + (f"  (${pnl_u:+.2f})" if pnl_u else "")
                    )
                if sl_moved and new_sl:
                    lines.append(f"   SL → {self._fmt_price(new_sl)}")
                elif dec.get("sl_reason"):
                    lines.append(f"   SL: {dec.get('sl_reason')}")

            elif hit:
                lines.append(f"✅ TP{tp}%  (no decision data)")

            elif not is_open:
                lines.append(f"✖ TP{tp}%  not reached")
                break

            else:
                tl_ts = trade.get("time_limit_expires_ts")
                if tl_ts:
                    remaining_h = max(0, (tl_ts - time.time()) / 3600)
                    lines.append(f"⏳ TP{tp}%  waiting  (time limit in {remaining_h:.1f}h)")
                else:
                    lines.append(f"⏳ TP{tp}%  waiting")
                break

        self._send(chat_id, "\n".join(lines))

    # ── /validate ──────────────────────────────────────────────────────

    def _cmd_validate(self, chat_id: str) -> None:
        signals = self._tracker.get_active_signals()

        if not signals:
            self._send(chat_id, "🔍 <b>VALIDATE</b>\n\nNo active signals to check.")
            return

        tp_targets = self._tracker.tp_targets

        cat_additional: list = []
        cat_signal: list = []
        cat_volume: list = []
        cat_outcome: list = []

        for s in signals:
            sym = s.get("symbol", "???")
            ad = s.get("additional_data", {})
            out = s.get("outcome", {})

            if not ad:
                cat_additional.append(f"{sym}: additional_data is empty")
            else:
                if ad.get("oi_growth_ratio") is None:
                    cat_additional.append(f"{sym}: oi_growth_ratio is null")
                if ad.get("funding_rate") is None:
                    cat_additional.append(f"{sym}: funding_rate is null")
                if ad.get("rvol_20") is None:
                    cat_additional.append(f"{sym}: rvol_20 is null")
                if ad.get("vol_24h_usdt") is None:
                    cat_additional.append(f"{sym}: vol_24h_usdt missing")
                if ad.get("vol_24h_base") is None:
                    cat_additional.append(f"{sym}: vol_24h_base missing")

            if "high_breakout_warning" not in s:
                cat_signal.append(f"{sym}: high_breakout_warning missing")

            for n in (1, 2, 3):
                if s.get(f"vol_candle_{n}_base") is None:
                    cat_volume.append(f"{sym}: vol_candle_{n}_base missing")

            for tp in tp_targets:
                key = f"tp{tp}_hit"
                if out.get(key) is None:
                    cat_outcome.append(f"{sym}: {key} missing from outcome")
                    break

        all_issues = cat_additional + cat_signal + cat_volume + cat_outcome
        problem_syms = set()
        for i in all_issues:
            problem_syms.add(i.split(":")[0])
        clean = len(signals) - len(problem_syms)

        lines = [
            "🔍 <b>VALIDATE</b>",
            "",
            f"📊 Total signals: {len(signals)}",
            f"✅ Clean: {clean}",
            f"⚠️ With issues: {len(problem_syms)}",
        ]

        if all_issues:
            shown = 0
            limit = 50
            for label, cat in [
                ("📋 Additional Data", cat_additional),
                ("📌 Signal Fields", cat_signal),
                ("📊 Volume Fields", cat_volume),
                ("🎯 Outcome Fields", cat_outcome),
            ]:
                if not cat or shown >= limit:
                    continue
                lines.append("")
                lines.append(f"<b>{label} ({len(cat)}):</b>")
                for i in cat:
                    if shown >= limit:
                        lines.append(f"  ... truncated")
                        break
                    lines.append(f"  • {i}")
                    shown += 1
        else:
            lines.append("")
            lines.append("🎉 All signals look clean!")

        self._send(chat_id, "\n".join(lines))

    # ── /slstatus ────────────────────────────────────────────────────

    def _cmd_slstatus(self, chat_id: str) -> None:
        """""
        Show all open paper trades with current price, SL level,
        distance to SL, and whether BTC dump SL was already applied.
        Useful during BTC dumps to see exposure at a glance.
        """""
        trades = self._load_paper_trades()
        if not trades:
            self._send(chat_id, "📊 No paper trades found.")
            return

        open_trades = [t for t in trades if t.get("status") == "open"]
        if not open_trades:
            self._send(chat_id, "📊 No open paper trades right now.")
            return

        try:
            prices = self._binance.get_mark_prices()
        except Exception:
            prices = {}

        open_trades.sort(key=lambda t: t.get("opened_ts", 0))

        lines = [f"🛡 <b>SL STATUS — {len(open_trades)} open trades</b>", ""]

        total_notional   = 0.0
        worst_sl_dist    = None
        worst_sl_sym     = ""

        for t in open_trades:
            sym    = t["symbol"]
            entry  = t.get("actual_entry_price") or t.get("entry_price", 0)
            sl     = t.get("sl_price", 0)
            sl_type = t.get("sl_type", "fixed")
            margin  = t.get("margin_used", 0)
            lev     = t.get("leverage", 10)
            notional = margin * lev
            cur    = prices.get(sym, 0)
            dump_applied = t.get("btc_dump_sl_applied", False)
            processed = t.get("processed_tps") or []
            remaining_pct = round(
                t.get("remaining_quantity", 0) / t.get("quantity", 1) * 100
            ) if t.get("quantity", 0) > 0 else 100

            # calc %s
            if entry > 0 and cur > 0:
                cur_vs_entry = (cur - entry) / entry * 100
            else:
                cur_vs_entry = None

            if cur > 0 and sl > 0:
                sl_dist = (cur - sl) / cur * 100   # how far current is above SL
            elif entry > 0 and sl > 0:
                sl_dist = (entry - sl) / entry * 100
            else:
                sl_dist = None

            if sl > 0 and entry > 0:
                sl_vs_entry = (sl - entry) / entry * 100
            else:
                sl_vs_entry = None

            total_notional += notional

            if sl_dist is not None:
                if worst_sl_dist is None or sl_dist < worst_sl_dist:
                    worst_sl_dist = sl_dist
                    worst_sl_sym  = sym

            # icon
            if cur_vs_entry is None:
                price_icon = "⬜"
            elif cur_vs_entry >= 5:
                price_icon = "🟢"
            elif cur_vs_entry >= 0:
                price_icon = "🟡"
            else:
                price_icon = "🔴"

            sl_type_icon = "🔄" if sl_type == "trailing" else "📌"
            dump_icon    = "⚡" if dump_applied else "  "

            # price line
            cur_str   = f"{cur:.6g}" if cur > 0 else "?"
            sl_str    = f"{sl:.6g}"  if sl > 0  else "?"
            entry_str = f"{entry:.6g}"

            cur_pct_str    = f"{cur_vs_entry:+.1f}%" if cur_vs_entry is not None else "?"
            sl_dist_str    = f"{sl_dist:.1f}% away"  if sl_dist is not None else "?"
            sl_entry_str   = f"{sl_vs_entry:+.1f}% from entry" if sl_vs_entry is not None else ""

            tp_str = ("✅ " + " ".join(p.upper() for p in processed)) if processed else "⏳ waiting TP5"

            lines.append(
                f"{price_icon}{dump_icon} <b>{sym}</b>  {cur_pct_str}  ({remaining_pct}% rem)"
            )
            lines.append(
                f"   💵 cur: {cur_str}  entry: {entry_str}"
            )
            lines.append(
                f"   {sl_type_icon} SL: {sl_str} ({sl_entry_str})  — {sl_dist_str}"
            )
            lines.append(
                f"   {tp_str}  | ${notional:.1f} notional"
            )
            lines.append("")

        # footer summary
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💼 Total notional: ${total_notional:.1f}")
        if worst_sl_dist is not None:
            lines.append(f"⚠️ Closest to SL: <b>{worst_sl_sym}</b> ({worst_sl_dist:.1f}% away)")
        lines.append("")
        lines.append("🔄 = trailing SL  📌 = fixed SL")
        lines.append("⚡ = BTC dump SL already applied")
        lines.append("🟢 ≥+5%  🟡 0-5%  🔴 negative")

        self._send(chat_id, "\n".join(lines))

    # ── /dumpalert ──────────────────────────────────────────────────────
    def _cmd_dumpalert(self, chat_id: str) -> None:
        """""
        Show all open trades with current price, SL, distance to SL,
        P&L vs entry, and BTC dump protection status.
        Sorted by most at-risk (closest to SL first).
        """""
        trades = self._load_paper_trades()
        if not trades:
            self._send(chat_id, "📊 No paper trades found.")
            return

        open_trades = [t for t in trades if t.get("status") == "open"]
        if not open_trades:
            self._send(chat_id, "✅ No open trades right now.")
            return

        try:
            prices = self._binance.get_mark_prices()
        except Exception:
            prices = {}

        rows = []
        for t in open_trades:
            sym          = t["symbol"]
            entry        = t.get("actual_entry_price") or t.get("entry_price", 0)
            sl           = t.get("sl_price", 0)
            margin       = t.get("margin_used", 0)
            lev          = t.get("leverage", 10)
            notional     = margin * lev
            cur          = prices.get(sym, 0)
            dump_applied = t.get("btc_dump_sl_applied", False)
            sl_type      = t.get("sl_type", "fixed")
            processed    = t.get("processed_tps") or []
            rem_qty      = t.get("remaining_quantity", 0)
            orig_qty     = t.get("quantity", 1) or 1
            rem_pct      = round(rem_qty / orig_qty * 100) if orig_qty > 0 else 100

            cur_vs_entry = (cur - entry) / entry * 100   if (entry > 0 and cur > 0)  else None
            sl_dist      = (cur - sl)   / cur   * 100   if (cur > 0 and sl > 0)      else None
            sl_vs_entry  = (sl  - entry)/ entry * 100   if (entry > 0 and sl > 0)    else None

            rows.append({
                "sym": sym, "cur": cur, "entry": entry, "sl": sl,
                "sl_type": sl_type, "notional": notional,
                "cur_vs_entry": cur_vs_entry, "sl_dist": sl_dist,
                "sl_vs_entry": sl_vs_entry, "dump_applied": dump_applied,
                "processed": processed, "rem_pct": rem_pct,
            })

        # sort: most at risk (smallest sl_dist) first
        rows.sort(key=lambda r: r["sl_dist"] if r["sl_dist"] is not None else 999)

        total_notional  = sum(r["notional"] for r in rows)
        at_risk         = [r for r in rows if r["sl_dist"] is not None and r["sl_dist"] < 5]
        dump_protected  = sum(1 for r in rows if r["dump_applied"])

        lines = [
            f"⚠️ <b>DUMP ALERT — {len(rows)} open trades</b>",
            f"₿ sorted by closest to SL  |  🔴 = &lt;5% from SL",
            "",
        ]

        for r in rows:
            sym         = r["sym"]
            cur_str     = f"{r['cur']:.6g}"   if r["cur"]  > 0 else "?"
            sl_str      = f"{r['sl']:.6g}"    if r["sl"]   > 0 else "?"
            entry_str   = f"{r['entry']:.6g}" if r["entry"]> 0 else "?"
            pnl_str     = f"{r['cur_vs_entry']:+.1f}%" if r["cur_vs_entry"] is not None else "?"
            dist_str    = f"{r['sl_dist']:.1f}% to SL" if r["sl_dist"] is not None else "?"
            sl_e_str    = f"{r['sl_vs_entry']:+.1f}% from entry" if r["sl_vs_entry"] is not None else ""

            # risk icon
            if r["sl_dist"] is not None and r["sl_dist"] < 3:
                risk = "🔴🔴"
            elif r["sl_dist"] is not None and r["sl_dist"] < 5:
                risk = "🔴"
            elif r["cur_vs_entry"] is not None and r["cur_vs_entry"] >= 5:
                risk = "🟢"
            elif r["cur_vs_entry"] is not None and r["cur_vs_entry"] >= 0:
                risk = "🟡"
            else:
                risk = "🔻"

            sl_icon     = "🔄" if r["sl_type"] == "trailing" else "📌"
            dump_icon   = "⚡" if r["dump_applied"] else "  "
            tp_str      = ("TPs: " + " ".join(p.upper() for p in r["processed"])) if r["processed"] else "no TP yet"

            lines.append(f"{risk}{dump_icon} <b>{sym}</b>  PnL: {pnl_str}  ({r['rem_pct']}% rem)")
            lines.append(f"   💵 cur: {cur_str}  entry: {entry_str}")
            lines.append(f"   {sl_icon} SL: {sl_str} ({sl_e_str})")
            lines.append(f"   📏 {dist_str}  |  {tp_str}  |  ${r['notional']:.1f}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💼 Total notional: ${total_notional:.1f}")
        lines.append(f"⚡ BTC dump protected: {dump_protected}/{len(rows)} trades")
        if at_risk:
            at_risk_syms = ", ".join(r["sym"] for r in at_risk)
            lines.append(f"🔴 Within 5% of SL: {at_risk_syms}")
        lines.append("")
        lines.append("⚡ = BTC dump SL already applied")
        lines.append("🔄 = trailing SL  📌 = fixed SL")
        lines.append("🔴🔴 = &lt;3% from SL  🔴 = &lt;5%  🟢 = profitable")

        self._send(chat_id, "\n".join(lines))


    # ══════════════════════════════════════════════════════════════════
    # TEST COMMANDS — verify REAL Binance API code path before going live
    # These commands call the ACTUAL trader/strategy_manager methods,
    # which place real orders on Binance (demo or live depending on config).
    # ══════════════════════════════════════════════════════════════════

    def _check_trader(self, chat_id: str) -> bool:
        """Guard: ensure trader is available and configured."""
        if not self._trader:
            self._send(chat_id,
                "❌ Trader not available.\n"
                "Make sure trading.enabled=true in config.json and bot restarted."
            )
            return False
        if not self._trader.enabled:
            self._send(chat_id,
                "❌ Trader is disabled (trading.enabled=false in config.json).\n"
                "Set enabled=true and restart bot."
            )
            return False
        return True

    def _load_trades_rw(self):
        import json
        p = self._data_dir / "trades.json"
        if not p.exists(): return []
        try: return json.load(open(p, encoding="utf-8"))
        except: return []

    def _load_sigs_rw(self):
        import json
        p = self._data_dir / "signals.json"
        if not p.exists(): return []
        try: return json.load(open(p, encoding="utf-8"))
        except: return []

    def _save_sigs_rw(self, sigs):
        import json
        p = self._data_dir / "signals.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sigs, f, indent=2)

    # ── /testopen SYMBOL [PRICE] ──────────────────────────────────────
    def _cmd_testopen(self, chat_id: str, args: list) -> None:
        """
        Open a REAL test trade on Binance (demo or live).
        Calls trader.place_trade() — same exact code path as a real signal.
        Places a real market order + real SL stop-market order on Binance.

        Usage: /testopen BTCUSDT
               /testopen ETHUSDT 3500
        """
        if not self._check_trader(chat_id): return

        if not args:
            self._send(chat_id,
                "❌ Usage: /testopen SYMBOL [PRICE]\n"
                "Example: /testopen BTCUSDT\n"
                "         /testopen ETHUSDT 3500"
            )
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"

        # Get price
        if len(args) >= 2:
            try: price = float(args[1])
            except: self._send(chat_id, "❌ Invalid price"); return
        else:
            try:
                prices = self._binance.get_mark_prices()
                price  = prices.get(symbol, 0)
                if price <= 0:
                    self._send(chat_id, f"❌ Could not fetch price for {symbol}")
                    return
            except Exception as e:
                self._send(chat_id, f"❌ Price fetch error: {e}"); return

        mode = "PAPER" if self._trader._paper_mode else "LIVE"
        is_testnet = getattr(self._trader._binance, "_testnet", False)
        env  = "DEMO/TESTNET" if is_testnet else "REAL LIVE"

        self._send(chat_id,
            f"🧪 Opening test trade...\n"
            f"Mode: {mode}  |  Env: {env}\n"
            f"Symbol: {symbol}  Price: {price:.6g}"
        )

        # Build minimal alert dict (same format scanner sends)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        alert = {
            "symbol":     symbol,
            "alert_time": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "entry_price": price,
            "_test_trade": True,
        }

        import time as _time
        # Snapshot trades BEFORE calling place_trade
        trades_before = set(
            t.get("trade_id","") for t in self._load_trades_rw()
        )

        try:
            # THIS IS THE REAL CODE PATH — same as when a signal fires
            self._trader.place_trade(symbol, price, alert)
        except Exception as e:
            self._send(chat_id, f"❌ place_trade() error:\n{e}")
            return

        # Wait briefly then check if a new trade record appeared
        _time.sleep(1.5)
        trades_after = self._load_trades_rw()
        new_trades   = [
            t for t in trades_after
            if t.get("trade_id","") not in trades_before
            and t.get("symbol") == symbol
        ]

        if new_trades:
            t = new_trades[0]
            mode = "📝 PAPER" if t.get("paper") else "🔴 LIVE"
            self._send(chat_id,
                f"✅ <b>{mode} TRADE OPENED — {symbol}</b>\n"
                f"Entry: {t.get('actual_entry_price',price):.6g}\n"
                f"SL:    {t.get('sl_price',0):.6g}\n"
                f"Qty:   {t.get('quantity',0)}\n"
                f"Margin: ${t.get('margin_used',0):.2f} × {t.get('leverage',10)}x\n\n"
                f"{'✅ Check Binance positions — order should be visible!' if not t.get('paper') else '📝 Paper mode — no real order placed'}\n\n"
                f"Next: /testtp {symbol} 5\n"
                f"      /testclose {symbol}"
            )
        else:
            # Trade not recorded — check why
            paper = self._trader._paper_mode
            has_creds = self._binance.has_trading_credentials()
            bal = None
            try: bal = self._binance.get_usdt_balance()
            except: pass

            self._send(chat_id,
                f"⚠️ <b>place_trade() ran but NO trade was recorded</b>\n\n"
                f"Diagnosis:\n"
                f"  paper_mode = {paper}\n"
                f"  has_api_keys = {has_creds}\n"
                f"  balance = {'$'+f'{bal:.2f}' if bal else 'None (fetch failed)'}\n\n"
                f"Common causes:\n"
                f"  • paper_mode=true in config (check trading.paper_mode)\n"
                f"  • API keys wrong or no futures permission\n"
                f"  • Demo balance = 0 (activate demo account on Binance)\n"
                f"  • Symbol already open\n\n"
                f"Check VPS logs: tail -f scanner.log | grep -i trader"
            )

    # ── /testtp SYMBOL LEVEL ─────────────────────────────────────────
    def _cmd_testtp(self, chat_id: str, args: list) -> None:
        """
        Simulate TP hit — updates signal flags then immediately triggers
        strategy_manager to process it. On live mode: places real partial
        close order on Binance and modifies the SL order.

        Usage: /testtp BTCUSDT 5    (TP5)
               /testtp BTCUSDT 10   (TP10)  etc.
        """
        if not self._check_trader(chat_id): return
        if not self._strategy_mgr:
            self._send(chat_id, "❌ Strategy manager not available"); return

        if len(args) < 2:
            self._send(chat_id,
                "❌ Usage: /testtp SYMBOL LEVEL\n"
                "Levels: 5 10 20 30 50 75 100\n"
                "Example: /testtp BTCUSDT 5"
            )
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"
        try: tp = int(args[1])
        except: self._send(chat_id, "❌ Invalid TP level"); return
        if tp not in [5,10,20,30,50,75,100]:
            self._send(chat_id, "❌ Level must be: 5 10 20 30 50 75 100"); return

        import time
        from datetime import datetime, timezone

        # Find the open trade for this symbol
        trades = self._load_trades_rw()
        trade  = next(
            (t for t in trades if t.get("symbol") == symbol and t.get("status") == "open"),
            None
        )
        if not trade:
            self._send(chat_id,
                f"❌ No open trade for {symbol}\n"
                f"Use /testopen {symbol} first"
            )
            return

        entry    = trade.get("actual_entry_price") or trade.get("entry_price", 0)
        tp_price = entry * (1 + tp / 100.0)
        opened_ts = trade.get("opened_ts", time.time())
        hours = (time.time() - opened_ts) / 3600

        # Update signal with TP hit — mark ALL TPs up to requested as hit
        sigs = self._load_sigs_rw()
        sig  = next(
            (s for s in sigs if s.get("symbol") == symbol
             and s.get("alert_time") == trade.get("signal_time")),
            None
        )
        if not sig:
            sig = next((s for s in sigs if s.get("symbol") == symbol), None)

        if not sig:
            self._send(chat_id,
                f"⚠️ No signal record for {symbol} — creating minimal one"
            )
            now = datetime.now(timezone.utc)
            sig = {
                "symbol": symbol,
                "alert_time": trade.get("signal_time", now.strftime("%Y-%m-%d %H:%M:%S UTC")),
                "alert_time_ts": opened_ts,
                "entry_price": entry,
                "highest_price": tp_price,
                "quality_score": 4,
                "soft_flags": 1,
                "outcome": {},
            }
            sigs.append(sig)

        outcome = sig.setdefault("outcome", {})
        for t in [5,10,20,30,50,75,100]:
            if t <= tp:
                outcome[f"tp{t}_hit"] = True
                outcome[f"tp{t}_hit_hours_after_entry"] = round(hours, 2)
                if not sig.get(f"tp{t}_snapshot"):
                    sig[f"tp{t}_snapshot"] = {
                        "price": entry * (1 + t/100),
                        "price_momentum_4h_pct": 4.5,
                        "price_momentum_1h_pct": 2.1,
                        "oi_change_pct": 12.0,
                        "market_cap_usd": 45_000_000,
                    }

        sig["highest_price"] = tp_price
        outcome["highest_price"] = tp_price
        self._save_sigs_rw(sigs)

        self._send(chat_id,
            f"⚙️ TP{tp} flagged for {symbol}\n"
            f"Price level: {tp_price:.6g} (+{tp}%)\n"
            f"Triggering strategy_manager NOW..."
        )

        # Force strategy_manager to run immediately (real API calls happen here)
        try:
            self._strategy_mgr._process_once()
            self._send(chat_id,
                f"✅ strategy_manager processed TP{tp} for {symbol}\n"
                f"Check Binance — partial close order should have fired.\n"
                f"Check /paper {symbol} for updated trade record."
            )
        except Exception as e:
            self._send(chat_id, f"❌ strategy_manager error:\n{e}")

    # ── /testsl SYMBOL ───────────────────────────────────────────────
    def _cmd_testsl(self, chat_id: str, args: list) -> None:
        """
        Simulate SL hit. On live: the SL stop-market order fires automatically
        when price crosses it — you can't manually trigger it.
        This command drops current price below SL in strategy_manager's view
        so the paper SL check fires (paper mode), or tells you to check Binance (live).

        Usage: /testsl BTCUSDT
        """
        if not self._check_trader(chat_id): return
        if not args:
            self._send(chat_id, "❌ Usage: /testsl SYMBOL\nExample: /testsl BTCUSDT")
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"

        trades = self._load_trades_rw()
        trade  = next(
            (t for t in trades if t.get("symbol") == symbol and t.get("status") == "open"),
            None
        )
        if not trade: self._send(chat_id, f"❌ No open trade for {symbol}"); return

        sl    = trade.get("sl_price", 0)
        entry = trade.get("entry_price", 0)

        if self._trader._paper_mode:
            self._send(chat_id,
                f"📝 Paper mode — SL fires when mark price <= {sl:.6g}\n"
                f"Strategy manager checks every 15s.\n"
                f"Current SL: {sl:.6g} ({((sl-entry)/entry*100):+.1f}% from entry)\n\n"
                f"To force it: wait for price to drop to {sl:.6g}\n"
                f"or use /testclose {symbol} to manually close."
            )
        else:
            self._send(chat_id,
                f"🔴 Live mode — SL is a real stop-market order on Binance.\n"
                f"SL price: {sl:.6g} ({((sl-entry)/entry*100):+.1f}% from entry)\n\n"
                f"It will fire automatically when {symbol} price hits {sl:.6g}.\n"
                f"Check 'Open Orders' on Binance to confirm SL order exists."
            )

    # ── /testdump ────────────────────────────────────────────────────
    def _cmd_testdump(self, chat_id: str) -> None:
        """
        Simulate BTC dump — triggers the REAL BTC dump watcher logic.
        In live mode: moves real SL orders on Binance.
        In paper mode: updates sl_price in trades.json.

        Usage: /testdump
        """
        if not self._check_trader(chat_id): return
        if not self._strategy_mgr:
            self._send(chat_id, "❌ Strategy manager not available"); return

        trades = self._load_trades_rw()
        open_t = [t for t in trades if t.get("status") == "open"]
        if not open_t:
            self._send(chat_id, "❌ No open trades to test dump on"); return

        # Inject fake BTC dump prices into mark prices cache temporarily
        # by calling _check_btc_dump with manipulated data
        try:
            prices = self._binance.get_mark_prices()
        except:
            prices = {}

        # Patch: temporarily override BTC price to simulate dump
        # We'll directly call _check_btc_dump after patching the candle fetch
        self._send(chat_id,
            f"⚙️ Triggering BTC dump logic on {len(open_t)} open trades...\n"
            f"This calls the REAL _check_btc_dump() in strategy_manager.\n"
            f"In live mode: real SL orders on Binance will be modified."
        )

        # Monkey-patch get_closed_klines temporarily to return dump candles
        original_klines = self._binance.get_closed_klines

        def fake_dump_klines(symbol, tf, limit):
            if symbol == "BTCUSDT":
                btc = prices.get("BTCUSDT", 50000)
                # Return 7 candles where BTC is -4% in 4h and -5% in 24h
                candles = []
                for i in range(limit):
                    candles.append({"close": btc * (1 + (i - limit + 1) * 0.008)})
                return candles
            return original_klines(symbol, tf, limit)

        try:
            self._binance.get_closed_klines = fake_dump_klines
            result = self._strategy_mgr._check_btc_dump(open_t, prices)
            self._binance.get_closed_klines = original_klines  # restore

            if result:
                self._send(chat_id,
                    f"✅ BTC dump watcher FIRED\n"
                    f"SL adjusted on open trades.\n"
                    f"Check /dumpalert for current SL levels.\n"
                    f"In live mode: check Binance Open Orders for updated SLs."
                )
            else:
                self._send(chat_id,
                    f"⚠️ BTC dump logic ran but no changes made\n"
                    f"(All trades may already have dump SL applied)"
                )
        except Exception as e:
            self._binance.get_closed_klines = original_klines  # always restore
            self._send(chat_id, f"❌ Dump test error:\n{e}")

    # ── /testemerge SYMBOL ───────────────────────────────────────────
    def _cmd_testemerge(self, chat_id: str, args: list) -> None:
        """
        Simulate emergency exit — sets highest_price to trigger reversal.
        Immediately calls strategy_manager to process.
        In live: places real close order on Binance.

        Usage: /testemerge BTCUSDT
        """
        if not self._check_trader(chat_id): return
        if not self._strategy_mgr:
            self._send(chat_id, "❌ Strategy manager not available"); return
        if not args:
            self._send(chat_id, "❌ Usage: /testemerge SYMBOL\nExample: /testemerge BTCUSDT")
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"

        trades = self._load_trades_rw()
        trade  = next(
            (t for t in trades if t.get("symbol") == symbol and t.get("status") == "open"),
            None
        )
        if not trade: self._send(chat_id, f"❌ No open trade for {symbol}"); return

        try:
            prices = self._binance.get_mark_prices()
            cur    = prices.get(symbol, trade.get("entry_price", 0))
        except:
            cur = trade.get("entry_price", 0)

        # Set highest_price very high so current = -16% from peak → triggers emergency
        sigs = self._load_sigs_rw()
        sig  = next((s for s in sigs if s.get("symbol") == symbol), None)
        if sig:
            peak = cur * 1.20   # fake peak = current + 20%
            sig["highest_price"] = peak
            sig.setdefault("outcome", {})["highest_price"] = peak
            self._save_sigs_rw(sigs)

        self._send(chat_id,
            f"⚙️ Emergency exit triggered for {symbol}\n"
            f"Set highest_price = current +20% → reversal = -16.7% from peak\n"
            f"Calling strategy_manager NOW..."
        )

        try:
            self._strategy_mgr._process_once()
            self._send(chat_id,
                f"✅ Emergency exit processed for {symbol}\n"
                f"In live mode: real close order placed on Binance.\n"
                f"Check /paper {symbol} for result."
            )
        except Exception as e:
            self._send(chat_id, f"❌ Emergency exit error:\n{e}")

    # ── /testtl SYMBOL ───────────────────────────────────────────────
    def _cmd_testtl(self, chat_id: str, args: list) -> None:
        """
        Simulate time limit expiry — sets time_limit_expires_ts to past.
        Triggers strategy_manager to close remaining position.
        In live: places real close order on Binance.

        Usage: /testtl BTCUSDT
        """
        if not self._check_trader(chat_id): return
        if not self._strategy_mgr:
            self._send(chat_id, "❌ Strategy manager not available"); return
        if not args:
            self._send(chat_id, "❌ Usage: /testtl SYMBOL\nExample: /testtl BTCUSDT")
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"

        import time, json
        trades = self._load_trades_rw()
        trade  = next(
            (t for t in trades if t.get("symbol") == symbol and t.get("status") == "open"),
            None
        )
        if not trade: self._send(chat_id, f"❌ No open trade for {symbol}"); return

        trade["time_limit_expires_ts"] = time.time() - 1
        p = self._data_dir / "trades.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2)

        self._send(chat_id,
            f"⚙️ Time limit expired for {symbol}\n"
            f"Calling strategy_manager NOW..."
        )
        try:
            self._strategy_mgr._process_once()
            self._send(chat_id,
                f"✅ Time limit exit processed\n"
                f"Check Binance for closed position."
            )
        except Exception as e:
            self._send(chat_id, f"❌ Time limit error:\n{e}")

    # ── /testclose SYMBOL ────────────────────────────────────────────
    def _cmd_testclose(self, chat_id: str, args: list) -> None:
        """
        Force-close an open position using the REAL close path.
        In live/demo: places a real reduce-only market order on Binance.

        Usage: /testclose BTCUSDT
        """
        if not self._check_trader(chat_id): return
        if not args:
            self._send(chat_id, "❌ Usage: /testclose SYMBOL\nExample: /testclose BTCUSDT")
            return

        symbol = args[0].upper()
        if not symbol.endswith("USDT"): symbol += "USDT"

        trades = self._load_trades_rw()
        trade  = next(
            (t for t in trades if t.get("symbol") == symbol and t.get("status") == "open"),
            None
        )
        if not trade: self._send(chat_id, f"❌ No open trade for {symbol}"); return

        try:
            prices = self._binance.get_mark_prices()
            cur    = prices.get(symbol, trade.get("entry_price", 0))
        except:
            cur = trade.get("entry_price", 0)

        qty   = trade.get("remaining_quantity") or trade.get("quantity", 0)
        entry = trade.get("entry_price", 0)

        if self._trader._paper_mode:
            # Paper: just mark closed in JSON
            import json
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            pnl_pct  = (cur - entry) / entry * 100 if entry else 0
            pnl_usdt = qty * (cur - entry)
            trade.update({
                "status": "closed_manual_test",
                "close_reason": "manual_test",
                "close_price": cur,
                "closed_at":   now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "pnl_pct":     round(pnl_pct, 4),
                "pnl_usdt":    round(pnl_usdt, 4),
            })
            p = self._data_dir / "trades.json"
            with open(p, "w", encoding="utf-8") as f:
                json.dump(trades, f, indent=2)
            self._send(chat_id,
                f"✅ Paper trade closed for {symbol}\n"
                f"Price: {cur:.6g}  PnL: {pnl_pct:+.2f}% (${pnl_usdt:+.2f})"
            )
        else:
            # Live: place real reduce-only market order
            try:
                order = self._binance.place_market_order_reduce(symbol, "SELL", qty)
                fill  = float(order.get("avgPrice") or cur)
                pnl   = qty * (fill - entry)
                self._send(chat_id,
                    f"✅ LIVE CLOSE ORDER placed for {symbol}\n"
                    f"Fill: {fill:.6g}  Qty: {qty}\n"
                    f"PnL: ${pnl:+.4f}\n"
                    f"Check Binance — position should be closed."
                )
            except Exception as e:
                self._send(chat_id, f"❌ Close order error:\n{e}")

    # ── /testliveorder ───────────────────────────────────────────────
    def _cmd_testliveorder(self, chat_id: str, args: list) -> None:
        """
        Simplest API test: places a tiny market order then immediately closes it.
        Verifies: API keys work, order placement works, Binance responds.

        Usage: /testliveorder          (BTCUSDT default)
               /testliveorder ETHUSDT
        """
        if not self._binance.has_trading_credentials():
            self._send(chat_id,
                "❌ No API keys set.\n"
                "Add api_key + api_secret to config.json"
            )
            return

        import time, math
        symbol = (args[0].upper() if args else "BTCUSDT")
        if not symbol.endswith("USDT"): symbol += "USDT"

        is_testnet = getattr(self._binance, "_testnet", False)
        env = "DEMO/TESTNET" if is_testnet else "⚠️ REAL LIVE"

        self._send(chat_id,
            f"🧪 API connection test — {symbol}\n"
            f"Environment: {env}\n"
            f"Placing smallest possible order then closing it..."
        )

        # Get price
        try:
            prices = self._binance.get_mark_prices()
            price  = prices.get(symbol, 0)
            if price <= 0:
                self._send(chat_id, f"❌ Could not get price for {symbol}"); return
            self._send(chat_id, f"✅ Price: {price:.6g}")
        except Exception as e:
            self._send(chat_id, f"❌ Price fetch failed: {e}"); return

        # Balance
        try:
            bal = self._binance.get_usdt_balance()
            self._send(chat_id, f"✅ Balance: ${bal:.2f} USDT")
        except Exception as e:
            self._send(chat_id, f"❌ Balance check failed: {e}"); return

        # Leverage
        try:
            self._binance.set_leverage(symbol, 5)
            self._send(chat_id, "✅ Leverage set to 5x")
        except Exception as e:
            self._send(chat_id, f"⚠️ Leverage: {e}")

        # Min qty
        try:
            step, _ = self._binance.get_symbol_precision(symbol)
            raw = 6.0 / price
            qty = round(math.floor(raw/step)*step, 8) if step > 0 else round(raw, 6)
            if qty <= 0:
                self._send(chat_id, "❌ Qty rounds to 0"); return
            self._send(chat_id, f"✅ Test qty: {qty} (${qty*price:.2f} notional)")
        except Exception as e:
            self._send(chat_id, f"❌ Precision error: {e}"); return

        # Place order
        try:
            order  = self._binance.place_market_order(symbol, "BUY", qty)
            fill   = float(order.get("avgPrice") or price)
            oid    = order.get("orderId", "?")
            self._send(chat_id,
                f"✅ BUY ORDER PLACED!\n"
                f"OrderID: {oid}  Fill: {fill:.6g}\n"
                f"👆 Check Binance positions NOW — you should see it!"
            )
        except Exception as e:
            self._send(chat_id,
                f"❌ Order placement FAILED:\n{e}\n\n"
                f"Check:\n"
                f"• API keys correct?\n"
                f"• testnet=true for demo keys?\n"
                f"• Sufficient balance?"
            )
            return

        time.sleep(3)

        # Close
        try:
            close  = self._binance.place_market_order_reduce(symbol, "SELL", qty)
            cfill  = float(close.get("avgPrice") or fill)
            pnl    = qty * (cfill - fill)
            self._send(chat_id,
                f"✅ POSITION CLOSED\n"
                f"Fill: {cfill:.6g}  PnL: ${pnl:+.4f}\n\n"
                f"🎉 <b>API TEST PASSED!</b>\n"
                f"Order placement ✅  Close ✅\n"
                f"Ready for live trading."
            )
        except Exception as e:
            self._send(chat_id,
                f"⚠️ Auto-close failed: {e}\n"
                f"Close manually on Binance!"
            )

    # ── /testall ─────────────────────────────────────────────────────
    def _cmd_testall(self, chat_id: str) -> None:
        """
        Full test sequence using REAL code paths.
        Runs: API check → open → TP5 → dump → close
        """
        import time
        self._send(chat_id,
            "🧪 <b>FULL LIVE CODE PATH TEST</b>\n\n"
            "Steps:\n"
            "1. API connection test\n"
            "2. Open real trade (BTCUSDT)\n"
            "3. Simulate TP5 hit → partial close\n"
            "4. Simulate BTC dump → SL adjust\n"
            "5. Force close remaining\n\n"
            "Starting in 2s..."
        )
        time.sleep(2)

        sym = "BTCUSDT"

        self._send(chat_id, "🔵 Step 1/5: Testing API connection...")
        self._cmd_testliveorder(chat_id, [sym])
        time.sleep(5)

        self._send(chat_id, "🟡 Step 2/5: Opening test trade...")
        self._cmd_testopen(chat_id, [sym])
        time.sleep(20)

        self._send(chat_id, "🟠 Step 3/5: Simulating TP5 hit...")
        self._cmd_testtp(chat_id, [sym, "5"])
        time.sleep(20)

        self._send(chat_id, "🔴 Step 4/5: Simulating BTC dump...")
        self._cmd_testdump(chat_id)
        time.sleep(5)

        self._send(chat_id, "⚫ Step 5/5: Closing test trade...")
        self._cmd_testclose(chat_id, [sym])

        self._send(chat_id,
            "\n✅ <b>TEST COMPLETE</b>\n"
            "If all steps passed with real Binance responses\n"
            "you are ready to go live. 🚀"
        )

    # ── end test commands ─────────────────────────────────────────────

    # ── /help ────────────────────────────────────────────────────────
    def _cmd_help(self, chat_id: str) -> None:
        text = (
            "🤖 <b>COMMANDS</b>\n\n"
            "/report — Performance overview of all active signals\n"
            "/report BTC — Detailed breakdown for one coin\n"
            "/summary — Win rates, averages, best/worst\n"
            "/active — Quick list of tracked signals\n"
            "/export — JSON file of all currently active signals\n"
            "/coin ETH — JSON file for a specific coin\n"
            "/detailed_report — JSON file of completed signals (≥7 days)\n"
            "/export_csv — Flat CSV of all signals for analysis\n"
            "/validate — Data integrity check on active signals\n\n"
            "📝 <b>Paper trading:</b>\n"
            "/paper — Account balance, open trades, closed stats + TP rates\n"
            "/paper BTC — Full detail on one paper trade (TP decisions, SL moves)\n"
            "/slstatus — SL status of all open paper trades\n"
            "/dumpalert — All open trades sorted by closest to SL\n\n"
            "🧪 <b>Test commands (testnet / live verification):</b>\n"
            "/testopen BTCUSDT — Open a test trade\n"
            "/testtp BTCUSDT 5 — Simulate TP5 hit (then 10, 20, 30...)\n"
            "/testsl BTCUSDT — Simulate SL hit\n"
            "/testdump — Simulate BTC dump SL protection\n"
            "/testemerge BTCUSDT — Simulate emergency exit\n"
            "/testtl BTCUSDT — Simulate time limit expiry\n"
            "/testclose BTCUSDT — Force close a test trade\n"
            "/testall — Run full automated test sequence\n"
            "/testliveorder — Place real tiny order on Binance to verify API\n\n"
            "/help — This message\n\n"
            f"📡 Tracking window: {self._tracker.max_age_hours}h\n"
            "🏔 Prices update every 5 min\n"
            "🎯 Auto TP alerts at configured targets\n"
            "⚠️ Auto reversal warnings\n\n"
            "<b>Signal criteria:</b>\n"
            "1️⃣ 1h close breaks last 24h high\n"
            "2️⃣ Last 3 candles volume increasing (min 2x ratio)\n"
            "3️⃣ 24h price change ≤ ±20%"
        )
        self._send(chat_id, text)
