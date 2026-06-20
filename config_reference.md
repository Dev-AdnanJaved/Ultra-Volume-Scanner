# config.json Reference

Complete reference for every field in `config.json`.
Format: `field` — **type** — accepted values — description

---

## `binance`

| Field | Type | Values | Description |
|---|---|---|---|
| `api_key` | string | any | Binance Futures API key. Leave blank and set via env var `BINANCE_API_KEY` instead. Only required when `trading.enabled = true` and `trading.paper_mode = false`. |
| `api_secret` | string | any | Binance Futures API secret. Leave blank and set via env var `BINANCE_API_SECRET` instead. |

---

## `telegram`

| Field | Type | Values | Description |
|---|---|---|---|
| `bot_token` | string | any | Token from @BotFather. Leave blank and set via env var `TELEGRAM_BOT_TOKEN` instead. |
| `chat_id` | string | any | Channel, group, or user ID to send alerts to. Leave blank and set via env var `TELEGRAM_CHAT_ID` instead. |

---

## `scanner`

| Field | Type | Values | Description |
|---|---|---|---|
| `timeframe` | string | `"1m"` `"5m"` `"15m"` `"1h"` `"4h"` | Candle timeframe used for volume and breakout detection. |
| `scan_interval_seconds` | int | e.g. `300` `600` `900` | How often to scan all symbols (seconds). `900` = every 15 min. |
| `breakout_lookback_candles` | int | e.g. `12` `24` `48` | Number of candles to look back when finding the 24h high for breakout detection. |
| `consecutive_vol_candles` | int | e.g. `2` `3` `4` | Number of recent candles that must all show elevated volume. |
| `consecutive_vol_min_ratio` | float | e.g. `1.5` `2.0` `3.0` | Each of the recent candles must have volume ≥ this × baseline average. |
| `high_breakout_warning_pct` | float | e.g. `3.0` `5.0` `10.0` | If the breakout margin exceeds this %, add a ⚠️ caution flag to the alert. |
| `max_price_change_24h_pct` | float | e.g. `15.0` `20.0` `30.0` | Hard filter: skip symbol if absolute 24h price change exceeds this %. |
| `min_volume_usdt` | int | e.g. `0` `1000000` | Hard filter: skip symbol if USDT volume < this. `0` = disabled. |
| `cooldown_hours` | int | e.g. `12` `24` `72` | Suppress re-alerts for the same symbol within this window (hours). |
| `excluded_symbols` | list | e.g. `["USDCUSDT"]` | Symbols to always skip regardless of any conditions. |

### `scanner.hard_filters`

All four must pass or the signal is dropped entirely.

| Field | Type | Values | Description |
|---|---|---|---|
| `vol_ratio_max` | float | e.g. `10` `15` `20` | Drop if RVOL > this — likely a wash-trade or data spike. |
| `funding_rate_min` | float | e.g. `-0.03` `-0.05` `-0.1` | Drop if funding rate < this (shorts paying heavily = bearish pressure). |
| `vol_24h_usdt_min` | int | e.g. `1000000` `5000000` | Drop if 24h USDT volume < this (illiquid market). |
| `market_cap_usd_max` | int | e.g. `100000000` `200000000` | Drop if market cap > this (large caps have different dynamics). |

### `scanner.soft_flags`

Each condition that triggers adds 1 to the warning count. If count ≥ `max_flags_to_block`, the signal is dropped.

| Field | Type | Values | Description |
|---|---|---|---|
| `rvol_min` | float | e.g. `1.5` `2.0` | Flag if RVOL < this (low relative volume for the timeframe). |
| `market_cap_usd_max` | int | e.g. `100000000` `200000000` | Flag if market cap > this. |
| `oi_growth_ratio_max` | float | e.g. `30` `50` `100` | Flag if OI growth ratio > this (extreme leverage build-up). |
| `funding_rate_min` | float | e.g. `-0.01` `-0.02` | Flag if funding rate < this. |
| `vol_24h_usdt_min` | int | e.g. `3000000` `5000000` | Flag if 24h USDT volume < this. |
| `price_change_24h_max` | float | e.g. `10.0` `15.0` `20.0` | Flag if absolute 24h price change > this % (already pumped). |
| `ema50_distance_pct_max` | float | e.g. `10.0` `15.0` | Flag if price is > this % above EMA50 (overextended). |
| `vol_ratio_max` | float | e.g. `10.0` `12.0` | Flag if RVOL > this (suspicious spike, different from hard filter). |
| `max_flags_to_block` | int | e.g. `3` `4` `5` | Drop signal if soft flag count ≥ this value. |

### `scanner.quality_score`

Each condition that passes awards 1 point. Maximum score = 8. Score shown in the Telegram alert.

| Field | Type | Values | Description |
|---|---|---|---|
| `rvol_sweet_spot_min` | float | e.g. `3.0` `4.0` | +1 if RVOL ≥ this. |
| `rvol_sweet_spot_max` | float | e.g. `8.0` `10.0` | +1 only if also RVOL ≤ this (sweet spot, not extreme). |
| `rvol_adequate_min` | float | e.g. `1.5` `2.0` | +1 if RVOL ≥ this (reward even if outside sweet spot). |
| `market_cap_usd_min` | int | e.g. `5000000` `10000000` | +1 if market cap ≥ this. |
| `market_cap_usd_max` | int | e.g. `50000000` `100000000` | +1 only if also market cap ≤ this (small cap sweet spot). |
| `oi_growth_ratio_min` | int | e.g. `3` `5` | +1 if OI growth ratio ≥ this. |
| `oi_growth_ratio_max` | int | e.g. `30` `50` | +1 only if also OI growth ratio ≤ this. |
| `funding_rate_min` | float | e.g. `0` `0.01` | +1 if funding rate ≥ this (longs not overpaying). |
| `vol_24h_usdt_min` | int | e.g. `5000000` `10000000` | +1 if 24h USDT volume ≥ this. |
| `breakout_margin_pct_min` | float | e.g. `0.3` `0.5` | +1 if breakout margin ≥ this % (real conviction). |
| `breakout_margin_pct_max` | float | e.g. `3.0` `5.0` | +1 only if also breakout margin ≤ this % (not overextended). |
| `price_change_24h_min` | float | e.g. `0` `1.0` | +1 if 24h price change ≥ this (positive momentum). |
| `price_change_24h_max` | float | e.g. `8.0` `10.0` | +1 only if also 24h change ≤ this % (not already pumped). |

### `scanner.btc_trend`

Classifies BTC as `ranging` / `pumping` / `dumping` each cycle using 4h and 24h price changes.

| Field | Type | Values | Description |
|---|---|---|---|
| `enabled` | bool | `true` `false` | Whether to classify and record BTC trend. |
| `skip_on_dump` | bool | `true` `false` | Skip entire scan cycle when BTC is dumping. |
| `dump_threshold_pct` | float | e.g. `-2.0` `-3.0` `-5.0` | BTC 4h AND 24h must both be below this % to classify as "dumping". |
| `pump_threshold_pct` | float | e.g. `2.0` `3.0` `5.0` | BTC 4h AND 24h must both be above this % to classify as "pumping". |

---

## `tracker`

| Field | Type | Values | Description |
|---|---|---|---|
| `enabled` | bool | `true` `false` | Master switch for price tracking and TP/reversal alerts. |
| `max_age_hours` | int | e.g. `72` `168` `336` | Forget signals older than this many hours. `168` = 7 days. |
| `price_update_interval_seconds` | int | e.g. `60` `300` `600` | How often the tracker refreshes prices for all open signals (seconds). |
| `data_dir` | string | e.g. `"data"` | Directory for `signals.json`, `trades.json`, `paper_account.json`, etc. |
| `take_profit_targets` | list | e.g. `[5, 10, 20, 30, 50, 75, 100]` | TP levels (%) to track and send alerts for. |
| `reversal_alert_enabled` | bool | `true` `false` | Send an alert when price reverses from its peak. |
| `min_reversal_peak_pct` | float | e.g. `2.0` `3.0` `5.0` | Only watch for reversal if signal peaked ≥ this % above entry. |
| `reversal_drop_from_peak_pct` | float | e.g. `3.0` `5.0` `10.0` | Send reversal alert if price drops ≥ this % from its peak. |
| `detailed_report_min_age_hours` | int | e.g. `24` `72` `168` | `/detailed_report` command only shows signals older than this. |
| `daily_report_hour` | int | `0`–`23` | UTC hour when the daily summary is sent. `0` = midnight UTC. |

---

## `market_cap`

| Field | Type | Values | Description |
|---|---|---|---|
| `enabled` | bool | `true` `false` | Fetch market cap data from CoinGecko. Disabling also disables market cap hard/soft filters. |
| `cache_minutes` | int | e.g. `60` `120` `240` | How long to cache CoinGecko data before re-fetching (minutes). |

---

## `rate_limit`

| Field | Type | Values | Description |
|---|---|---|---|
| `binance_delay_ms` | int | e.g. `100` `200` `500` | Minimum delay between Binance API calls (ms). Increase if getting HTTP 429 errors. |

---

## `trading`

| Field | Type | Values | Description |
|---|---|---|---|
| `enabled` | bool | `true` `false` | Master switch. `false` = no trades placed at all. |
| `paper_mode` | bool | `true` `false` | `true` = simulate all trades with zero real orders; `false` = live trading. |
| `paper_starting_balance` | float | e.g. `500.0` `1000.0` `10000.0` | Fake USDT starting balance for paper mode P&L tracking. |
| `margin_type` | string | `"pct"` `"fixed"` | `"pct"` = use a percentage of free balance; `"fixed"` = use a fixed USDT amount. |
| `margin_value` | float | e.g. `2` `5` `10` `100` | If `pct`: percentage of balance (e.g. `5` = 5%). If `fixed`: USDT amount (e.g. `50` = $50). |
| `leverage` | int | e.g. `5` `10` `20` `50` `125` | Preferred leverage to request on Binance. Falls back if unavailable. |
| `leverage_step` | int | e.g. `5` `10` | Step to reduce leverage by if preferred is unavailable (e.g. 20 → 15 → 10). |
| `leverage_min` | int | e.g. `3` `5` `10` | Skip signal entirely if leverage cannot be set at least this high. |
| `sl_pct` | float | e.g. `5.0` `10.0` `15.0` | Stop-loss distance from entry price (%). `15.0` = SL at -15% from entry. |
| `max_open_trades` | int | e.g. `1` `3` `5` `10` | Hard cap on concurrent open positions. |
| `check_interval_seconds` | int | e.g. `30` `60` `120` | How often Trader polls Binance to detect manually closed positions (seconds). |

---

## `exit_strategy`

| Field | Type | Values | Description |
|---|---|---|---|
| `enabled` | bool | `true` `false` | Master switch for the TP ladder / trailing SL / emergency exit system. |
| `initial_sl_pct` | float | e.g. `10.0` `15.0` `20.0` | Initial SL distance from entry (%). Overrides `trading.sl_pct` when enabled. |
| `check_interval_seconds` | int | e.g. `30` `60` `120` | Poll interval for live trades (seconds). |
| `paper_check_interval_seconds` | int | e.g. `5` `10` `15` | Poll interval in paper mode (seconds). Tighter than live for better SL detection accuracy. |
| `tp5_slow_exit_hours` | float | e.g. `12.0` `24.0` `48.0` | Exit 100% at TP5 if it took longer than this many hours (slow signal = weak momentum). |

### `exit_strategy.time_limits_hours`

If the next TP level is not hit within this window after the previous one, exit the remaining position.

| Field | Type | Values | Description |
|---|---|---|---|
| `tp5_to_tp10` | int | e.g. `24` `48` `72` | Hours allowed to go from TP5 hit to TP10 hit. |
| `tp10_to_tp20` | int | e.g. `48` `72` `96` | Hours allowed to go from TP10 to TP20. |
| `tp20_to_tp30` | int | e.g. `24` `48` | Hours allowed to go from TP20 to TP30. |
| `tp30_to_tp50` | int | e.g. `24` `48` | Hours allowed to go from TP30 to TP50. |
| `tp50_to_tp75` | int | e.g. `24` `48` | Hours allowed to go from TP50 to TP75. |
| `tp75_to_tp100` | int | e.g. `12` `24` | Hours allowed to go from TP75 to TP100. |

### `exit_strategy.close_pcts`

How much of the remaining position to close at each TP hit.
For TP5/10/20/30 the percentage depends on the momentum score (0–10). For TP50/75/100 a flat % is always used.

| Field | Type | Values | Description |
|---|---|---|---|
| `tp5_score_01` | int | `0`–`100` | % to close at TP5 when score is 0–1 (very weak momentum). |
| `tp5_score_23` | int | `0`–`100` | % to close at TP5 when score is 2–3. |
| `tp5_score_45` | int | `0`–`100` | % to close at TP5 when score is 4–5. |
| `tp5_score_6plus` | int | `0`–`100` | % to close at TP5 when score is 6–10 (strong — mostly hold). |
| `tp10_score_01` | int | `0`–`100` | % to close at TP10 when score is 0–1. |
| `tp10_score_23` | int | `0`–`100` | % to close at TP10 when score is 2–3. |
| `tp10_score_45` | int | `0`–`100` | % to close at TP10 when score is 4–5. |
| `tp10_score_6plus` | int | `0`–`100` | % to close at TP10 when score is 6–10. |
| `tp20_score_02` | int | `0`–`100` | % to close at TP20 when score is 0–2. |
| `tp20_score_35` | int | `0`–`100` | % to close at TP20 when score is 3–5. |
| `tp20_score_68` | int | `0`–`100` | % to close at TP20 when score is 6–8. |
| `tp30_score_02` | int | `0`–`100` | % to close at TP30 when score is 0–2 (last scored exit before trailing takes over). |
| `tp30_score_35` | int | `0`–`100` | % to close at TP30 when score is 3–5. |
| `tp30_score_68` | int | `0`–`100` | % to close at TP30 when score is 6–8 (`0` = ride fully, update SL only). |
| `tp50` | int | `0`–`100` | Flat % to close at TP50 (score not used). |
| `tp75` | int | `0`–`100` | Flat % to close at TP75 (score not used). |
| `tp100` | int | `0`–`100` | Flat % to close at TP100; remaining closed later by the 2-red-4h-candle rule. |

### `exit_strategy.sl_ratchet`

Where to move the stop-loss after each TP is hit.

| Field | Type | Values | Description |
|---|---|---|---|
| `tp5_pct` | float | e.g. `0.0` `2.0` `5.0` | After TP5 (low score): move SL to entry + this %. `0.0` = move to breakeven. |
| `tp5_ride_keep_sl` | bool | `true` `false` | `true` = high-score TP5 keeps the original SL (no ratchet yet). |
| `tp10_pct` | float | e.g. `3.0` `5.0` `8.0` | After TP10 (low score): move SL to entry + this % (locked in profit). |
| `tp10_ride_pct` | float | e.g. `0.0` `2.0` | After TP10 (high score / ride): move SL to entry + this %. |
| `tp20_pct` | float | e.g. `8.0` `12.0` `15.0` | After TP20: move SL to entry + this %. |
| `tp30_trail_pct` | float | e.g. `8.0` `10.0` `12.0` | After TP30: switch to trailing SL this % below the running high. |
| `tp50_trail_pct` | float | e.g. `6.0` `8.0` `10.0` | After TP50: tighten trailing SL to this % below the running high. |
| `tp75_trail_pct` | float | e.g. `6.0` `8.0` `10.0` | After TP75: trailing SL maintained at this % below the running high. |
| `tp100_trail_pct` | float | e.g. `10.0` `12.0` `15.0` | After TP100: widen trail slightly (price is more volatile at these levels). |

### `exit_strategy.emergency_exit`

Conditions that trigger an immediate close outside the normal TP cycle.

| Field | Type | Values | Description |
|---|---|---|---|
| `btc_dump_enabled` | bool | `true` `false` | When BTC dumps (4h AND 24h both < –3%), move SL to –5% from current price on all open trades. |
| `funding_spike_pct` | float | e.g. `0.1` `0.3` `0.5` | Exit live trade if funding rate exceeds this % per 8h period (over-leveraged market). `0` = disabled. |
| `reversal_from_peak_pct` | float | e.g. `10.0` `15.0` `20.0` | Emergency-exit if price drops more than this % from its running high since entry. |

---

## `logging`

| Field | Type | Values | Description |
|---|---|---|---|
| `level` | string | `"DEBUG"` `"INFO"` `"WARNING"` `"ERROR"` `"CRITICAL"` | Log verbosity. `"DEBUG"` shows every API call; `"INFO"` is normal operation; `"WARNING"` or higher for quiet mode. |
| `log_file` | string | e.g. `"scanner.log"` | Path to the log file (relative to project root). |
