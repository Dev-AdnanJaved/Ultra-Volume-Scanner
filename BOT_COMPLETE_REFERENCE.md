# Binance Futures Volume Scanner — Complete Reference

## What the Bot Does

The bot scans every active Binance USDT-M perpetual futures pair (typically 300–400 pairs) every **15 minutes** looking for breakout signals. When a signal fires, it immediately records a full data snapshot and then tracks the coin's price performance for **7 days (168 hours)**. During those 7 days it updates prices every **5 minutes**, records event-driven journal entries when meaningful things happen, takes a full market-data snapshot every time a take-profit level is hit, and finally closes + archives the signal with a complete outcome summary when the 7-day window expires.

---

## PHASE 1 — SIGNAL DETECTION (Entry Conditions)

Runs every **15 minutes** on every symbol. All checks are sequential — any failure stops processing that symbol.

### Step 1 — Pre-checks (instant, no API calls)
| Check | What it does |
|---|---|
| Excluded symbols | Skips `USDCUSDT`, `BTCDOMUSDT` |
| Already tracked | Skips any coin already being tracked in this 7-day window |
| Cooldown | Skips any coin that fired a signal within the last **72 hours** |

### Step 2 — BTC Trend Filter (once per scan cycle, not per symbol)
Fetches last 7 closed **4h BTC candles**. Calculates:
- `btc_chg_4h` — % change from 1 candle ago to now
- `btc_chg_24h` — % change from 6 candles ago to now
- `avg_chg = (btc_chg_4h + btc_chg_24h) / 2`

| avg_chg | Classification | Effect |
|---|---|---|
| ≤ -3.0% | **dumping** | ENTIRE scan cycle is skipped — no signals fire |
| ≥ +3.0% | **pumping** | Signals still fire normally |
| between | **ranging** | Signals still fire normally |

### Step 3 — Main Criteria (ALL 3 must pass)

#### Criterion 1 — 24h Breakout
- Fetches last **26 closed 1h candles**
- Finds the **highest high** of candles 1–24 (the 24h lookback window)
- **Requires:** current candle close > that 24h highest high
- Calculates `breakout_margin_pct = (close − 24h_high) / 24h_high × 100`

#### Criterion 2 — Consecutive Volume Increase (last 3 candles)
- Takes the last 3 closed 1h candles
- **Requires:** volume candle3 > candle2 > candle1 (strictly increasing in USDT quote volume)
- **Requires:** `vol_ratio = candle3_vol / candle1_vol ≥ 2.0×`

#### Criterion 3 — 24h Price Change Cap
- From the pre-fetched 24h ticker
- **Requires:** `|price_change_24h_pct| ≤ 20%`

### Step 4 — Additional Data Collection
After the 3 main criteria pass, extra market data is fetched (none of these block the signal — failures are silently skipped):

| Field | Source | What it measures |
|---|---|---|
| `rvol_20` | Last 21 closed 1h candles | Current candle USDT vol ÷ 20-candle baseline average |
| `vol_baseline_avg` | Same 21 candles | Average USDT volume of the 20-candle baseline |
| `oi_current_usdt` | OI history API (25 × 1h periods) | Current open interest in USD |
| `oi_avg_24h_usdt` | Same 25 periods | Average OI over prior 24 periods |
| `oi_change_pct` | Derived | (current OI − avg OI) / avg OI × 100 |
| `oi_growth_current` | Derived | Last 1h OI change in USD |
| `oi_growth_avg` | Derived | Average of prior 1h OI changes |
| `oi_growth_ratio` | Derived | oi_growth_current ÷ \|oi_growth_avg\| (surge multiplier) |
| `funding_rate` | Binance premiumIndex | Current funding rate (converted to %) |
| `funding_in_ideal_range` | Derived | True if −0.02% ≤ funding ≤ 0.15% |
| `vol_24h_usdt` | 24h ticker | Total 24h USDT volume |
| `vol_24h_above_50m` | Derived | True if vol_24h_usdt ≥ $50M |
| `vol_24h_base` | 24h ticker | Total 24h volume in base coin units |
| `ema50_4h` | Last 55 closed 4h candles | 50-period EMA on the 4h timeframe |
| `price_above_ema50_4h` | Derived | True if current close > EMA50 |
| `ema50_distance_pct` | Derived | (close − EMA50) / EMA50 × 100 |
| `volatility_recent_10_pct` | Last 10 closed 1h candles | Avg candle range % (high−low)/close for recent 10 |
| `volatility_prior_10_pct` | Prior 10 closed 1h candles | Same for candles −20 to −10 |
| `volatility_compression_ratio` | Derived | recent_range / prior_range (< 0.7 = compressed) |
| `is_compressed` | Derived | True if compression_ratio < 0.7 |
| `market_cap_usd` | CoinGecko (cached 2h) | Coin market cap in USD |
| `market_cap_fmt` | Derived | Human-readable e.g. "$45.2M" |

### Step 5 — Hard Filters (ALL 4 must pass or signal is blocked)

| Filter | Threshold | What it checks |
|---|---|---|
| Vol ratio cap | `vol_ratio ≤ 15×` | Prevents insane spike-and-dump |
| Funding rate floor | `funding_rate > −0.05%` | Avoids heavily shorted coins |
| 24h volume minimum | `vol_24h_usdt > $5,000,000` | Ensures basic liquidity |
| Market cap maximum | `market_cap_usd < $1,000,000,000` | Avoids mega caps |

### Step 6 — Soft Flags (8 possible; 4+ blocks the signal)

Each condition that is true adds 1 flag. 4 or more flags = signal blocked:

| Flag name | Triggered when |
|---|---|
| `low_rvol` | rvol_20 < 2.0× |
| `large_mcap` | market_cap_usd > $200,000,000 |
| `extreme_oi` | oi_growth_ratio > 50 |
| `neg_funding` | funding_rate < −0.02% |
| `low_vol` | vol_24h_usdt < $5,000,000 |
| `extreme_chg` | \|price_change_24h\| > 15% |
| `far_ema` | ema50_distance_pct > 15% |
| `high_vol_ratio` | vol_ratio > 12× |

### Step 7 — Quality Score (0–8 points)

Each condition that is true adds 1 point. Higher = better signal:

| Point name | Condition |
|---|---|
| `rvol_sweet` | 4.0 ≤ rvol_20 ≤ 8.0× (sweet spot from backtest) |
| `rvol_ok` | rvol_20 ≥ 2.0× (adequate volume) |
| `small_mcap` | $10M ≤ market_cap ≤ $50M (best performing range) |
| `oi_moderate` | 5 ≤ oi_growth_ratio ≤ 50 (healthy OI growth) |
| `funding_ok` | funding_rate ≥ 0% (neutral or positive) |
| `vol_24h_ok` | vol_24h_usdt ≥ $10,000,000 (good liquidity) |
| `brk_conviction` | 0.5% ≤ breakout_margin_pct ≤ 5% (not too weak, not overextended) |
| `momentum_ok` | 0% ≤ price_change_24h ≤ 10% (positive but not extreme) |

---

## PHASE 2 — SIGNAL RECORDED AT ENTRY (Once, at T=0)

When all checks pass, the signal is recorded to `data/signals.json` and a Telegram alert is sent.

### Root-level Signal Fields (stored once at entry)

| Field | Type | Description |
|---|---|---|
| `symbol` | string | e.g. `"XYZUSDT"` |
| `entry_price` | float | Mark price at the exact moment signal fires |
| `highest_price` | float | Running max price since entry (starts = entry_price) |
| `lowest_price` | float | Running min price since entry (starts = entry_price) |
| `current_price` | float | Most recent tracked price |
| `alert_time_ts` | float | Unix timestamp of signal e.g. 1716230400.0 |
| `alert_time` | string | Human-readable UTC e.g. "2026-05-20 12:00:00 UTC" |
| `timeframe` | string | Always `"1h"` |
| `price_change_24h` | float | 24h price change % at entry |
| `breakout_margin_pct` | float | How far above 24h high the close is (%) |
| `high_breakout_warning` | bool | True if breakout_margin_pct > 5% |
| `high_24h` | float | The actual 24h highest-high price that was broken |
| `vol_candle_1` | float | USDT volume of oldest of the 3 consecutive candles |
| `vol_candle_2` | float | USDT volume of middle candle |
| `vol_candle_3` | float | USDT volume of most recent (trigger) candle |
| `vol_candle_1_fmt` | string | Formatted e.g. "$1.23M" |
| `vol_candle_2_fmt` | string | Formatted |
| `vol_candle_3_fmt` | string | Formatted |
| `vol_candle_1_base` | float | Volume in base coin units (candle 1) |
| `vol_candle_2_base` | float | Volume in base coin units (candle 2) |
| `vol_candle_3_base` | float | Volume in base coin units (candle 3) |
| `vol_candle_1_base_fmt` | string | Formatted base units e.g. "1.23M" |
| `vol_candle_2_base_fmt` | string | Formatted base units |
| `vol_candle_3_base_fmt` | string | Formatted base units |
| `vol_ratio` | float | candle3_vol / candle1_vol e.g. 3.47 |
| `candle_colors` | list[string] | Color of each of the 3 candles e.g. `["green","green","green"]` |
| `rvol` | float | Same as rvol_20 — current vol ÷ 20-candle average |
| `btc_price` | float | BTC mark price at the exact moment of signal |
| `candle_time` | string | Open time of the trigger candle e.g. "2026-05-20 11:00 UTC" |
| `soft_flags` | int | Count of soft flags raised (0–8) |
| `soft_flag_details` | list[string] | List of flag description strings e.g. `["low_rvol 1.5x<2.0x"]` |
| `quality_score` | int | Quality score (0–8) |
| `quality_details` | list[string] | List of quality criteria met e.g. `["rvol_ok","funding_ok"]` |
| `additional_data` | dict | All 22 fields from Step 4 (see table above) |
| `btc_trend_at_entry` | string | BTC trend at signal time: "ranging" / "pumping" / "unknown" |
| `btc_trend_detail` | dict | `{btc_chg_4h, btc_chg_24h, btc_close}` |
| `tp_sent` | list[int] | TP levels already alerted (starts empty `[]`) |
| `reversal_warned` | bool | Whether a reversal warning was sent (starts `false`) |
| `outcome` | dict | Full outcome block — see next section |
| `price_journey` | list[dict] | Event-based price journal (starts empty `[]`) |

**Total root fields at entry: ~38 root + 22 additional_data = ~60 distinct fields**

### Outcome Block (initialized at entry, updated every 5 minutes)

| Field | Type | Description |
|---|---|---|
| `max_drawdown_pct` | float | Worst % below entry so far (negative number, e.g. −3.5) |
| `max_drawdown_time` | string | UTC timestamp when max drawdown was set |
| `max_drawdown_hours_after_entry` | float | Hours after entry when max DD occurred |
| `went_negative_before_tp` | bool | True if price went below entry before ANY TP was hit |
| `hours_negative_total` | float | Cumulative hours the signal spent below entry price |
| `peak_pct` | float | Best % above entry so far (e.g. 12.4) |
| `peak_time` | string | UTC timestamp when peak was set |
| `peak_hours_after_entry` | float | Hours after entry when peak occurred |
| `signal_type` | string | Live: "active". At archive: "fast"/"slow"/"delayed"/"failed" |
| `signal_closed` | bool | False until archived at 7 days |
| `close_reason` | string | null until archived, then "expired" |
| `close_time` | string | null until archived |
| `btc_change_entry_to_tp` | float | BTC % change from entry to the FIRST TP hit (null if no TP) |
| `btc_trend_during_signal` | string | "pumping"/"ranging"/"dumping" — finalized at archive |
| `tp5_hit` | bool | Whether +5% target was reached |
| `tp5_hit_time` | string | UTC time it was hit (null if not hit) |
| `tp5_hit_hours_after_entry` | float | Hours after entry (null if not hit) |
| `tp5_max_drawdown_before` | float | Max drawdown recorded before this TP |
| `tp5_btc_price_at_hit` | float | BTC mark price at moment TP5 was confirmed |
| *(same 5 sub-fields for tp10, tp20, tp30, tp50, tp75, tp100)* | | |

**Total outcome fields: 14 core + (5 × 7 TP levels) = 49 fields**

---

## PHASE 3 — POST-ENTRY TRACKING (Every 5 Minutes for 7 Days)

### Price Update Cycle (every 5 minutes)
The tracker background loop runs every **300 seconds**. Per 7-day lifecycle:
- **2,016 price update cycles** (168 hours × 12 per hour)

Each cycle does one bulk mark-price API call for ALL tracked coins. Per signal, it updates:
- `current_price` — latest mark price
- `highest_price` — updated if current > previous highest
- `lowest_price` — updated if current < previous lowest
- `last_update_ts` — unix timestamp of this update
- Recalculates in `outcome`: `max_drawdown_pct`, `max_drawdown_time`, `max_drawdown_hours_after_entry`, `peak_pct`, `peak_time`, `peak_hours_after_entry`, `went_negative_before_tp`, `hours_negative_total`, `signal_type`

### Price Journey Snapshots (Event-Based, not every 5 minutes)
A journey snapshot is only written when one or more trigger events occur. Events can be combined in a single snapshot (e.g. `"new_high+4h_checkpoint"`):

| Event name | Trigger condition |
|---|---|
| `new_high` | Current price > previous highest ever recorded for this signal |
| `new_low` | Current price < previous lowest ever recorded |
| `below_entry` | Price drops below entry price (only when it was previously above it) |
| `btc_move` | BTC has moved ≥ 2% from the BTC level at the last journey snapshot |
| `4h_checkpoint` | At least 4 hours have passed since the last `4h_checkpoint` event |
| `tp_hit_N` | A TP target N% was just confirmed hit (added by TP check step) |

**Guaranteed minimum: 42 journey snapshots** (one 4h checkpoint every 4 hours × 42 times in 168 hours).  
**Typical total: 50–100 journey entries per signal** (more for volatile coins with many new highs/lows/BTC moves).

#### Every Journey Snapshot Contains These 12 Fields

| Field | Description |
|---|---|
| `event` | Event name(s) joined by "+" e.g. `"new_high+4h_checkpoint"` |
| `timestamp` | UTC string e.g. "2026-05-20 16:00:00 UTC" |
| `timestamp_ts` | Unix timestamp (float) |
| `hours_after_entry` | Hours since signal fired (float, e.g. 8.25) |
| `price` | Current price at snapshot time |
| `pct_from_entry` | % change from entry price (e.g. +4.7) |
| `btc_price` | BTC mark price at snapshot time |
| `btc_pct_from_signal_entry` | BTC % change since the signal was first fired |
| `volume_1h` | Last closed 1h USDT volume (fetched live from Binance) |
| `volume_1h_base` | Last closed 1h base coin volume (fetched live) |
| `is_new_low` | bool — True if this snapshot records a new all-time low for this signal |
| `is_new_high` | bool — True if this snapshot records a new all-time high for this signal |

---

## PHASE 4 — TAKE-PROFIT SNAPSHOTS (Up to 7 Times Per Signal)

TP levels tracked: **+5%, +10%, +20%, +30%, +50%, +75%, +100%**

A TP is "hit" when `highest_price` reaches that % above entry. Once hit, it never un-hits. When a new TP level is reached:
1. The `outcome` block is updated with hit time, hours, drawdown before, and BTC price
2. A **full market snapshot** is fetched (all same fields as entry additional_data + 3 momentum fields unique to TP snapshots)
3. The snapshot is stored on the signal root as `tp5_snapshot`, `tp10_snapshot`, etc.
4. A journey snapshot with event `"tp_hit_N"` is written
5. A Telegram alert is sent

**Maximum TP snapshots per signal: 7** (one per level, each fetched fresh from Binance at the moment it's hit)

#### Every TP Snapshot Contains These ~33 Fields

| Field | Description |
|---|---|
| `hit_time` | UTC string when TP was confirmed |
| `hit_hours_after_entry` | Hours elapsed since signal entry |
| `max_drawdown_before` | Worst drawdown % recorded BEFORE this TP was hit |
| `btc_price_at_hit` | BTC mark price at moment of TP hit |
| `btc_pct_change_since_entry` | BTC % change from signal entry to this TP hit |
| `rvol_20` | Current candle vol ÷ 20-candle baseline (recalculated fresh) |
| `vol_baseline_avg` | Baseline average USDT volume at TP time |
| `volume_1h` | Last 1h USDT volume at TP time |
| `volume_1h_base` | Last 1h base coin volume at TP time |
| `oi_current_usdt` | Open interest in USD at TP time |
| `oi_avg_24h_usdt` | Average OI over prior 24h at TP time |
| `oi_change_pct` | OI % change vs 24h average at TP time |
| `oi_growth_current` | Last 1h OI growth in USD at TP time |
| `oi_growth_avg` | Average 1h OI growth at TP time |
| `oi_growth_ratio` | OI surge multiplier at TP time |
| `funding_rate` | Funding rate % at TP time |
| `funding_in_ideal_range` | bool — True if −0.02% ≤ funding ≤ 0.15% |
| `vol_24h_usdt` | 24h USDT volume at TP time |
| `vol_24h_above_50m` | bool |
| `vol_24h_base` | 24h base coin volume at TP time |
| `ema50_4h` | 4h EMA50 value at TP time |
| `price_above_ema50_4h` | bool — True if current price is above EMA50 |
| `ema50_distance_pct` | % distance of current price from EMA50 at TP time |
| `volatility_recent_10_pct` | Avg candle range % for last 10 × 1h candles at TP time |
| `volatility_prior_10_pct` | Avg candle range % for prior 10 × 1h candles at TP time |
| `volatility_compression_ratio` | recent / prior range ratio |
| `is_compressed` | bool — True if ratio < 0.7 |
| `market_cap_usd` | Market cap at TP time (from CoinGecko cache) |
| `market_cap_fmt` | Formatted market cap string |
| `price_momentum_1h_pct` | **TP-only field** — % change of last closed 1h candle |
| `price_momentum_4h_pct` | **TP-only field** — % change of last closed 4h candle |
| `candle_colors_at_hit` | **TP-only field** — last 3 × 1h candle colors `["green","red","green"]` |

*The last 3 fields (price_momentum_1h_pct, price_momentum_4h_pct, candle_colors_at_hit) exist ONLY in TP snapshots — they are not in entry additional_data.*

---

## PHASE 5 — SIGNAL ARCHIVE / CLOSE (At T = 168 Hours)

At every 5-minute tracker cycle, the bot checks if any signal is ≥ 168 hours old. When found, it stamps final fields and compresses to a monthly gzip archive.

### Fields Added at Archive (to signal root)

| Field | Description |
|---|---|
| `archived_time_ts` | Unix timestamp of archival |
| `archived_time` | UTC string e.g. "2026-05-27 12:05:00 UTC" |
| `tracked_hours` | Total hours tracked (e.g. 168.1) |
| `peak_pct` | Best % from entry over entire 7 days |
| `lowest_pct` | Worst % from entry over entire 7 days |
| `exit_pct` | Final % from entry at the archive moment |
| `exit_price` | Price at archive moment |
| `highest_pct` | Alias of peak_pct |
| `market_cap_usd_exit` | Market cap at archive time (from CoinGecko cache) |
| `market_cap_exit_fmt` | Formatted string |

### Outcome Block Finalization at Archive

| Field | Value set |
|---|---|
| `signal_type` | `"fast"` (first TP < 6h) / `"slow"` (first TP 6–72h) / `"delayed"` (first TP > 72h) / `"failed"` (no TP hit) |
| `signal_closed` | `true` |
| `close_reason` | `"expired"` |
| `close_time` | UTC timestamp string |
| `btc_trend_during_signal` | `"pumping"` / `"ranging"` / `"dumping"` — based on BTC % change from entry to first TP (or to archive if no TP): pumping if +2%, dumping if −2%, ranging otherwise |

The signal is then saved to: **`data/signals_YYYY_MM.json.gz`** (gzip-compressed monthly file, ~70–80% smaller than raw JSON)

---

## COMPLETE 7-DAY DATA COLLECTION SUMMARY

| What | How often | Fields written per occurrence |
|---|---|---|
| **Signal entry** | Once at T=0 | ~38 root + 22 additional_data + 49 outcome fields = **~109 fields total** |
| **Price update** | Every 5 min × 2,016 times | 4 live fields + outcome recalculation (9 fields refreshed) |
| **Journey snapshot** | Event-driven ~50–100 times | **12 fields** per snapshot |
| **TP snapshot** | Up to 7 times (once per TP level hit) | **~33 fields** per snapshot |
| **Archive close** | Once at T=168h | 10 archive root fields + 5 outcome finalization fields |

### Total Data Volume Per Signal (7-Day Lifecycle)

| Data section | Count | Fields |
|---|---|---|
| Root + additional_data at entry | 1 | ~60 fields |
| Outcome block (init + updates) | 1 block, updated 2,016× | 49 fields |
| Journey snapshots | ~70 entries | 70 × 12 = ~840 data points |
| TP snapshots | Up to 7 | 7 × 33 = up to 231 fields |
| Archive fields | 1 set | 15 fields |

---

## DATA FLOW — FULL LIFECYCLE DIAGRAM

```
EVERY 15 MINUTES — Scanner Cycle
│
├── [Once per cycle] Fetch all symbols + mark prices + 24h tickers
│
├── [Once per cycle] BTC Trend Check
│   └── Fetches 7 × 4h BTC candles → classifies "ranging/pumping/dumping"
│   └── If DUMPING → skip entire cycle (no signals this round)
│
└── For each untracked, uncooled, non-excluded symbol:
    │
    ├── Fetch 26 closed 1h candles
    ├── CHECK 1: close > 24h highest high? → breakout_margin_pct
    ├── CHECK 2: last 3 candles strictly increasing vol AND ratio ≥ 2×?
    ├── CHECK 3: |24h price change| ≤ 20%?
    │
    ├── [If all 3 pass] Collect additional_data:
    │   ├── Fetch 25 × 1h OI history → oi_current, oi_avg, oi_change_pct,
    │   │   oi_growth_current, oi_growth_avg, oi_growth_ratio
    │   ├── Fetch funding rate → funding_rate, funding_in_ideal_range
    │   ├── Use 24h ticker → vol_24h_usdt, vol_24h_above_50m, vol_24h_base
    │   ├── Fetch 55 × 4h candles → ema50_4h, price_above_ema50_4h,
    │   │   ema50_distance_pct
    │   ├── Use existing 1h candles → rvol_20, vol_baseline_avg,
    │   │   volatility_recent_10_pct, volatility_prior_10_pct,
    │   │   volatility_compression_ratio, is_compressed
    │   └── CoinGecko cache → market_cap_usd, market_cap_fmt
    │
    ├── Apply 4 HARD FILTERS (vol_ratio ≤15, funding >−0.05,
    │   vol_24h >$5M, mcap <$1B) → fail = blocked
    │
    ├── Count SOFT FLAGS (0–8) → 4+ = blocked
    │
    ├── Calculate QUALITY SCORE (0–8)
    │
    └── SIGNAL FIRES
        ├── Save full signal to data/signals.json (~109 fields)
        ├── Send Telegram breakout alert
        └── Start 7-day tracking lifecycle

═══════════════════════════════════════════════════════════

EVERY 5 MINUTES — Tracker Cycle
│
├── Fetch all mark prices (single bulk API call)
│
├── For each active signal:
│   ├── Update current/highest/lowest price + timestamp
│   ├── Recalculate outcome (drawdown, peak, hours_negative, signal_type)
│   │
│   ├── Journey snapshot check (event-driven):
│   │   ├── new_high? → fetch 1h volume → write 12-field snapshot
│   │   ├── new_low? → same
│   │   ├── below_entry (first time)? → same
│   │   ├── BTC moved ≥2% from last snapshot? → same
│   │   ├── 4+ hours since last checkpoint? → same
│   │   └── (multiple events merge into one snapshot with "+" joining)
│   │
│   └── TP level check (levels: 5/10/20/30/50/75/100%):
│       └── For any newly reached TP:
│           ├── Mark tp{N}_hit, tp{N}_hit_time, tp{N}_hit_hours_after_entry,
│           │   tp{N}_max_drawdown_before, tp{N}_btc_price_at_hit
│           ├── Fetch full market snapshot (25×1h, 25 OI, funding, 55×4h, mcap)
│           │   → save ~33-field tp{N}_snapshot to signal
│           ├── Write journey event "tp_hit_N"
│           └── Send Telegram TP alert
│
└── Archive signals ≥ 168 hours old:
    ├── Stamp archive fields (archived_time, tracked_hours, peak_pct,
    │   lowest_pct, exit_pct, exit_price, market_cap_usd_exit...)
    ├── Finalize outcome (signal_type, signal_closed, close_reason,
    │   close_time, btc_trend_during_signal)
    └── Compress to data/signals_YYYY_MM.json.gz
```

---

## FILE STORAGE

| File | Contents |
|---|---|
| `data/signals.json` | All currently active signals (< 7 days old) |
| `data/signals_YYYY_MM.json.gz` | Compressed monthly archives of all expired signals |
| `data/history.json` | Legacy history file (older format signals) |
| `data/pending_report.json` | Signals queued for the daily Telegram report |
| `data/last_report_date.txt` | Date of last daily report sent (prevents duplicates) |
| `scanner.log` | Rolling application log |

---

## TELEGRAM ALERTS SENT PER SIGNAL LIFECYCLE

| Alert type | When | Key content |
|---|---|---|
| **Breakout Signal** | T=0, once | Symbol, price, breakout %, 3-candle USDT + base volumes, RVOL, 24h change, BTC trend, quality score/8, soft flags/8 |
| **TP +5%** | When highest_price ≥ entry × 1.05 | Symbol, entry price, peak price, current price, % gains, signal age |
| **TP +10%** | When highest_price ≥ entry × 1.10 | Same format |
| **TP +20%** | When highest_price ≥ entry × 1.20 | Same format |
| **TP +30%** | When highest_price ≥ entry × 1.30 | Same format |
| **TP +50%** | When highest_price ≥ entry × 1.50 | Same format |
| **TP +75%** | When highest_price ≥ entry × 1.75 | Same format |
| **TP +100%** | When highest_price ≥ entry × 2.00 | Same format |
| **Reversal Warning** | Once: if peak ≥ 3% then price drops 5% from peak | Symbol, entry, peak, current, drop from peak % |
| **Daily Report** | Once per day at configured UTC hour | JSON file attachment of all signals that completed in last 24h |

---

## TELEGRAM COMMANDS (Query the Bot Interactively)

| Command | What it returns |
|---|---|
| `/active` | Quick list of all currently tracked symbols |
| `/report` | Table of all active signals: current %, peak %, TP levels hit |
| `/report SYMBOL` | Full detailed breakdown for one coin (entry, price, outcome, TP history with snapshots) |
| `/summary` | Aggregate win rates, averages for active + archived signals |
| `/export` | Full JSON file of all signals (active + archived) |
| `/export_csv` | Flat CSV with every field as its own column (entry data, additional_data as `add_*`, outcome as `out_*`, TP snapshots as `tp10_*`, etc.) |
| `/detailed_report` | JSON file of signals that completed ≥ 7 days ago |
| `/help` | Command reference list |
