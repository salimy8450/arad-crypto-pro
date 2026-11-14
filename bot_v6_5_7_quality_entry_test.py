import os
import json
import time
import math
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V6.5.7 EARLY 5M LONG/SHORT TEST OURBIT
# CONFIRMED-ONLY TELEGRAM ALERTS
# Strict quality gates | MTF | BOS | SWEEP | PULLBACK | RISK FILTERS
# MTF | BOS | SWEEP | PULLBACK | RISK FILTERS
# ANALYSIS ONLY - NO ORDERS
# ==========================================================

BASE_URL = "https://futures.ourbit.io"
TELEGRAM_API = "https://api.telegram.org"

MAX_STAGE1_SYMBOLS = 300  # liquid top-300 universe for reliable 5m test cadence
MAX_STAGE2_SYMBOLS = 80
MAX_STAGE3_SYMBOLS = 60
MAX_TELEGRAM_WATCH_ALERTS = 0  # WATCH alerts are disabled
MAX_EARLY_ALERTS = 0  # EARLY alerts are disabled
TELEGRAM_CONFIRMED_ONLY = True

TIMEFRAMES = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
}

CANDLE_LIMIT = 220
MIN_CANDLES = 80
REQUEST_TIMEOUT = 18
REQUEST_PAUSE = 0.12

MIN_VOLUME_RATIO = 0.75
MIN_SCORE_WATCH = 5
MIN_SCORE_TRADE = 10  # higher score threshold for fewer, stronger candidates
MIN_RR = 2.00
MAX_STOP_ATR = 1.60  # reject setups with an excessively wide stop
MAX_LIVE_DIFF = 0.20  # do not chase a price that moved away from the closed 5m candle
ATR_SL_MULTIPLIER = 1.20
PIVOT_LOOKBACK = 12
STRUCTURE_SCAN_BARS = 5
SWEEP_LOOKBACK = 4
PULLBACK_MAX_ATR = 1.50
CONTINUATION_MAX_EMA21_ATR = 1.80
CONTINUATION_MIN_BODY_RATIO = 0.40
CONTINUATION_CLOSE_EDGE = 0.30
CONTINUATION_LOOKBACK_BARS = 3
CONTINUATION_BREAKOUT_LOOKBACK = 6
CONTINUATION_MIN_DIRECTIONAL_BARS = 2
MICRO_BREAKOUT_LOOKBACK = 4
MICRO_BREAKOUT_MIN_BODY = 0.45
EXTREME_RSI_SHORT_15M = 32.0
EXTREME_RSI_SHORT_5M = 28.0
EXTREME_RSI_LONG_15M = 68.0
EXTREME_RSI_LONG_5M = 72.0
# Higher-timeframe exhaustion guard for confirmed entries.
EXTREME_RSI_LONG_1H = 75.0
EXTREME_RSI_SHORT_1H = 25.0
CONFIRM_MIN_VOLUME_RATIO = 1.00
TRIGGER_MIN_BODY_RATIO = 0.45
TRIGGER_CLOSE_EDGE = 0.25

# Persistent anti-duplicate settings
WATCH_COOLDOWN_MIN = 45
CONFIRMED_COOLDOWN_MIN = 180
STATE_FILE = "arad_v65_7_test_signal_state.json"
OUTCOME_FILE = "arad_signal_outcomes.json"
EARLY_COOLDOWN_MIN = 15
CANDLE_CACHE = {}

EXCLUDED_BASES = {
    "USDC", "USDE", "DAI", "FDUSD", "TUSD", "USDP",
    "EUR", "GBP", "AUD", "JPY", "BRL", "TRY",
    "INDEX", "TEST",
}
METAL_KEYWORDS = ("GOLD", "XAU", "SILVER", "XAG")
PRINT_ALL_STAGES = True


def log(message):
    print(message, flush=True)


def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def as_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def http_json(url, method="GET", payload=None, headers=None, timeout=REQUEST_TIMEOUT):
    body = None
    request_headers = {"User-Agent": "ARAD-CRYPTO-PRO/6.5.7", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def api_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        data = http_json(url)
        if isinstance(data, dict):
            if data.get("success") is False:
                return None
            if data.get("code") not in (None, 0, "0"):
                return None
        return data
    except Exception as exc:
        log("API ERROR {}: {}".format(path, str(exc)[:160]))
        return None


def extract_data(payload):
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def get_contracts():
    data = extract_data(api_get("/api/v1/contract/detail"))
    if isinstance(data, dict):
        for key in ("contracts", "list", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


def get_tickers():
    data = extract_data(api_get("/api/v1/contract/ticker"))
    if isinstance(data, dict):
        for key in ("list", "rows", "tickers"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    result = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("symbol"):
                result[str(item["symbol"]).upper()] = item
    return result


def contract_is_enabled(contract):
    state = str(contract.get("state", contract.get("status", "0"))).upper()
    return state in ("0", "ENABLED", "ONLINE", "TRADING", "ACTIVE", "OPEN")


def is_target_symbol(symbol):
    symbol = str(symbol).upper()
    if not symbol.endswith("_USDT"):
        return False
    base = symbol[:-5]
    if base in EXCLUDED_BASES or len(base) < 2:
        return False
    return True


def ticker_turnover(ticker):
    return max(
        as_float(ticker.get("amount24")),
        as_float(ticker.get("turnover24")),
        as_float(ticker.get("quoteVolume")),
        as_float(ticker.get("volume24")),
    )


def ticker_price(ticker):
    return as_float(ticker.get("lastPrice", ticker.get("last", ticker.get("close"))))


def parse_candles(payload, interval_seconds):
    data = extract_data(payload)
    if not isinstance(data, dict):
        return []
    times = data.get("time") or data.get("timestamp") or []
    opens, highs, lows = data.get("open") or [], data.get("high") or [], data.get("low") or []
    closes = data.get("close") or []
    volumes = data.get("vol") or data.get("volume") or []
    candles = []

    if all(isinstance(x, list) for x in (times, opens, highs, lows, closes)):
        count = min(len(times), len(opens), len(highs), len(lows), len(closes))
        for i in range(count):
            ts = as_float(times[i])
            if ts > 10**12:
                ts /= 1000.0
            c = {
                "time": ts, "open": as_float(opens[i]), "high": as_float(highs[i]),
                "low": as_float(lows[i]), "close": as_float(closes[i]),
                "volume": as_float(volumes[i]) if i < len(volumes) else 0.0,
            }
            if ts > 0 and min(c["open"], c["high"], c["low"], c["close"]) > 0:
                candles.append(c)
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                ts = as_float(row.get("time", row.get("timestamp", row.get("t"))))
                c = {
                    "time": ts, "open": as_float(row.get("open", row.get("o"))),
                    "high": as_float(row.get("high", row.get("h"))),
                    "low": as_float(row.get("low", row.get("l"))),
                    "close": as_float(row.get("close", row.get("c"))),
                    "volume": as_float(row.get("vol", row.get("volume", row.get("v")))),
                }
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts = as_float(row[0])
                c = {
                    "time": ts, "open": as_float(row[1]), "high": as_float(row[2]),
                    "low": as_float(row[3]), "close": as_float(row[4]),
                    "volume": as_float(row[5]) if len(row) > 5 else 0.0,
                }
            else:
                continue
            if c["time"] > 10**12:
                c["time"] /= 1000.0
            if c["time"] > 0 and min(c["open"], c["high"], c["low"], c["close"]) > 0:
                candles.append(c)

    candles.sort(key=lambda x: x["time"])
    unique = {c["time"]: c for c in candles}
    now = time.time()
    return [c for c in unique.values() if c["time"] + interval_seconds <= now]


def get_candles(symbol, timeframe):
    cache_key = (symbol, timeframe)
    if cache_key in CANDLE_CACHE:
        return CANDLE_CACHE[cache_key]
    interval, seconds = TIMEFRAMES[timeframe]
    end_time = int(time.time())
    start_time = end_time - seconds * CANDLE_LIMIT
    payload = api_get(
        "/api/v1/contract/kline/" + urllib.parse.quote(symbol, safe=""),
        {"interval": interval, "start": start_time, "end": end_time},
    )
    candles = parse_candles(payload, seconds)
    result = candles if len(candles) >= MIN_CANDLES else []
    CANDLE_CACHE[cache_key] = result
    return result


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * multiplier + result * (1.0 - multiplier)
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]["close"]
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev), abs(cur["low"] - prev)))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def volume_ratio(candles, period=20):
    if len(candles) < period + 1:
        return 0.0
    old = [c["volume"] for c in candles[-period - 1:-1]]
    average = sum(old) / len(old)
    return candles[-1]["volume"] / average if average > 0 else 0.0


def impulse_volume_ok(candles, direction, baseline_period=20, recent_bars=3):
    """
    Confirm volume over a short impulse window instead of relying only on
    the final candle. This avoids rejecting a strong multi-candle move merely
    because the latest closed candle has below-average volume.
    """
    if len(candles) < baseline_period + recent_bars:
        return False

    recent = candles[-recent_bars:]
    baseline = candles[-(baseline_period + recent_bars):-recent_bars]
    baseline_volumes = [c["volume"] for c in baseline if c["volume"] > 0]
    if len(baseline_volumes) < max(10, baseline_period // 2):
        return False

    average_volume = sum(baseline_volumes) / len(baseline_volumes)
    if average_volume <= 0:
        return False

    recent_average = sum(c["volume"] for c in recent) / recent_bars
    window_ratio = recent_average / average_volume
    if window_ratio < 0.85:
        return False

    # Require at least one decisive candle in the intended direction.
    for candle in recent:
        candle_range = max(candle["high"] - candle["low"], 1e-12)
        body_ratio = abs(candle["close"] - candle["open"]) / candle_range
        close_position = (candle["close"] - candle["low"]) / candle_range
        if direction == "SHORT":
            if candle["close"] < candle["open"] and body_ratio >= 0.50 and close_position <= 0.35:
                return True
        else:
            if candle["close"] > candle["open"] and body_ratio >= 0.50 and close_position >= 0.65:
                return True
    return False


def ema_series(values, period):
    if len(values) < period:
        return []
    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    result = [None] * (period - 1) + [current]
    for value in values[period:]:
        current = value * multiplier + current * (1.0 - multiplier)
        result.append(current)
    return result


def macd_histogram(values):
    if len(values) < 35:
        return 0.0
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    macd_line = [
        (fast[i] - slow[i]) if fast[i] is not None and slow[i] is not None else None
        for i in range(len(values))
    ]
    valid = [x for x in macd_line if x is not None]
    if len(valid) < 9:
        return 0.0
    signal = ema_series(valid, 9)
    return valid[-1] - signal[-1] if signal and signal[-1] is not None else 0.0


def market_metrics(candles):
    closes = [c["close"] for c in candles]
    e9, e21, e50, e200 = (ema(closes, p) for p in (9, 21, 50, 200))
    e21_prev = ema(closes[:-1], 21) if len(closes) > 22 else e21
    if e200 is None:
        e200 = e50
    last = closes[-1]
    trend = "NEUTRAL"
    # Robust trend definition: short EMA alignment + EMA21 slope + price vs EMA50.
    # EMA200 is a context filter, not a hard requirement, avoiding needless neutral states.
    if e9 and e21 and e50 and e21_prev and last > e9 > e21 and e21 >= e21_prev and last > e50:
        trend = "LONG"
    elif e9 and e21 and e50 and e21_prev and last < e9 < e21 and e21 <= e21_prev and last < e50:
        trend = "SHORT"
    return {
        "price": last, "ema9": e9, "ema21": e21, "ema50": e50, "ema200": e200,
        "rsi": rsi(closes), "macd_hist": macd_histogram(closes), "atr": atr(candles),
        "volume_ratio": volume_ratio(candles), "trend": trend,
    }


def structure_signal(candles):
    """Find a valid closed-candle BOS/sweep in the latest 5 candles using confirmed swing levels."""
    if len(candles) < PIVOT_LOOKBACK + 5:
        return "NONE", False, False

    first = max(PIVOT_LOOKBACK + 1, len(candles) - STRUCTURE_SCAN_BARS)
    events = []

    for i in range(first, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        start = max(0, i - PIVOT_LOOKBACK)
        prior = candles[start:i]
        if len(prior) < 5:
            continue

        # Use the latest confirmed 2-left/2-right swing high and low.
        # The two right-side candles must already be closed before the event candle.
        swing_highs = []
        swing_lows = []
        for j in range(max(start + 2, 2), i - 1):
            high = candles[j]["high"]
            low = candles[j]["low"]
            if high > candles[j-1]["high"] and high > candles[j-2]["high"] and high >= candles[j+1]["high"] and high >= candles[j+2]["high"]:
                swing_highs.append((j, high))
            if low < candles[j-1]["low"] and low < candles[j-2]["low"] and low <= candles[j+1]["low"] and low <= candles[j+2]["low"]:
                swing_lows.append((j, low))

        resistance = swing_highs[-1][1] if swing_highs else max(c["high"] for c in prior)
        support = swing_lows[-1][1] if swing_lows else min(c["low"] for c in prior)

        bullish_bos = current["close"] > resistance and previous["close"] <= resistance
        bearish_bos = current["close"] < support and previous["close"] >= support

        sweep_window = candles[max(0, i - SWEEP_LOOKBACK):i]
        recent_low = min(c["low"] for c in sweep_window)
        recent_high = max(c["high"] for c in sweep_window)
        bullish_sweep = current["low"] < recent_low and current["close"] > recent_low and current["close"] > current["open"]
        bearish_sweep = current["high"] > recent_high and current["close"] < recent_high and current["close"] < current["open"]

        if bullish_bos:
            events.append((i, "LONG", False, True))
        elif bearish_bos:
            events.append((i, "SHORT", False, True))
        elif bullish_sweep:
            events.append((i, "LONG", True, False))
        elif bearish_sweep:
            events.append((i, "SHORT", True, False))

    if not events:
        return "NONE", False, False
    _, direction, sweep, bos = events[-1]
    return direction, sweep, bos

def micro_breakout_signal(candles, direction, min_volume_ratio=MIN_VOLUME_RATIO):
    """A directional break of the prior 4 completed candles; volume is checked separately."""
    lookback = MICRO_BREAKOUT_LOOKBACK
    if len(candles) < lookback + 2:
        return False
    last = candles[-1]
    prior = candles[-lookback-1:-1]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    if body_ratio < MICRO_BREAKOUT_MIN_BODY:
        return False
    if direction == "LONG":
        return last["close"] > max(x["high"] for x in prior) and last["close"] > last["open"]
    return last["close"] < min(x["low"] for x in prior) and last["close"] < last["open"]

def pullback_ok(metrics, direction, candles=None):
    """Require a recent EMA21 retest and a close back on the trend side."""
    price, e21, current_atr = metrics["price"], metrics["ema21"], metrics["atr"]
    if not e21 or current_atr <= 0:
        return False
    if abs(price - e21) / current_atr > PULLBACK_MAX_ATR:
        return False
    if direction == "LONG" and price < e21:
        return False
    if direction == "SHORT" and price > e21:
        return False
    if candles and len(candles) >= 4:
        recent = candles[-4:]
        tolerance = current_atr * 0.30
        touched = any(c["low"] <= e21 + tolerance for c in recent) if direction == "LONG" else any(c["high"] >= e21 - tolerance for c in recent)
        if not touched:
            return False
    return True

def momentum_continuation_ok(candles, metrics, direction):
    """
    Multi-candle continuation: evaluate the last 3 CLOSED 5m candles as one
    impulse, rather than demanding that the newest candle alone do all the work.
    This is only an entry-path detector; all shared trend, structure, volume,
    live-price and risk gates remain mandatory.
    """
    need = CONTINUATION_LOOKBACK_BARS + CONTINUATION_BREAKOUT_LOOKBACK
    if len(candles) < need or metrics["atr"] <= 0 or not metrics["ema21"]:
        return False

    recent = candles[-CONTINUATION_LOOKBACK_BARS:]
    prior = candles[-need:-CONTINUATION_LOOKBACK_BARS]
    last = recent[-1]
    extension = abs(last["close"] - metrics["ema21"]) / metrics["atr"]
    if extension > CONTINUATION_MAX_EMA21_ATR:
        return False

    directional = 0
    for candle in recent:
        if direction == "LONG" and candle["close"] > candle["open"]:
            directional += 1
        elif direction == "SHORT" and candle["close"] < candle["open"]:
            directional += 1
    if directional < CONTINUATION_MIN_DIRECTIONAL_BARS:
        return False

    impulse_high = max(c["high"] for c in recent)
    impulse_low = min(c["low"] for c in recent)
    impulse_range = max(impulse_high - impulse_low, 1e-12)
    impulse_open = recent[0]["open"]
    impulse_close = recent[-1]["close"]
    impulse_body_ratio = abs(impulse_close - impulse_open) / impulse_range
    close_position = (impulse_close - impulse_low) / impulse_range
    if impulse_body_ratio < CONTINUATION_MIN_BODY_RATIO:
        return False

    # Accept either a wider range break or a fresh break of the immediately
    # preceding three-candle micro range. This catches orderly continuations
    # without requiring an unusually large 7-candle breakout.
    near_prior = candles[-(CONTINUATION_LOOKBACK_BARS + 3):-CONTINUATION_LOOKBACK_BARS]
    if len(near_prior) < 3:
        return False

    if direction == "LONG":
        return (
            impulse_close > impulse_open
            and close_position >= 1.0 - CONTINUATION_CLOSE_EDGE
            and (
                impulse_close > max(c["high"] for c in prior)
                or last["close"] > max(c["high"] for c in near_prior)
            )
            and metrics["rsi"] >= 48
        )

    return (
        impulse_close < impulse_open
        and close_position <= CONTINUATION_CLOSE_EDGE
        and (
            impulse_close < min(c["low"] for c in prior)
            or last["close"] < min(c["low"] for c in near_prior)
        )
        and metrics["rsi"] <= 52
    )


def higher_timeframe_rsi_ok(m1, direction):
    # Hard block only at clear exhaustion; moderate extremes are score penalties.
    if direction == "LONG":
        return m1["rsi"] < EXTREME_RSI_LONG_1H
    return m1["rsi"] > EXTREME_RSI_SHORT_1H


def rsi_exhaustion_penalty(m1, m15, direction):
    penalty = 0
    if direction == "LONG":
        penalty += 2 if m1["rsi"] >= 70 else 1 if m1["rsi"] >= 65 else 0
        penalty += 2 if m15["rsi"] >= 72 else 1 if m15["rsi"] >= 68 else 0
    else:
        penalty += 2 if m1["rsi"] <= 30 else 1 if m1["rsi"] <= 35 else 0
        penalty += 2 if m15["rsi"] <= 28 else 1 if m15["rsi"] <= 32 else 0
    return penalty


def late_entry_ok(m15, m5, direction):
    # Avoid chasing exhausted moves on both setup and trigger timeframes.
    if direction == "SHORT":
        return not (m15["rsi"] < EXTREME_RSI_SHORT_15M or m5["rsi"] < EXTREME_RSI_SHORT_5M)
    return not (m15["rsi"] > EXTREME_RSI_LONG_15M or m5["rsi"] > EXTREME_RSI_LONG_5M)


def confirmed_directional_context_ok(m15, m5, candles5, direction, bos15=False, sweep15=False):
    """
    Strict confirmation gate for trade-plan alerts.
    Early impulse alerts remain a separate path; CONFIRMED must show an
    aligned 15m trend and a real 5m trigger, not just a green/red candle.
    """
    if not candles5 or len(candles5) < 3:
        return False

    last = candles5[-1]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range

    if direction == "LONG":
        m15_aligned = (
            m15["trend"] == "LONG"
            and m15["price"] > m15["ema21"]
            and m15["ema9"] > m15["ema21"]
            and m15["macd_hist"] > 0
            and m15["rsi"] >= 50
        )
        m5_trigger = (
            last["close"] > last["open"]
            and body_ratio >= 0.40
            and close_position >= 0.60
            and m5["price"] >= m5["ema9"]
            and m5["rsi"] >= 48
        )
    else:
        m15_aligned = (
            m15["trend"] == "SHORT"
            and m15["price"] < m15["ema21"]
            and m15["ema9"] < m15["ema21"]
            and m15["macd_hist"] < 0
            and m15["rsi"] <= 50
        )
        m5_trigger = (
            last["close"] < last["open"]
            and body_ratio >= 0.40
            and close_position <= 0.40
            and m5["price"] <= m5["ema9"]
            and m5["rsi"] <= 52
        )

    # A countertrend reversal is not a routine CONFIRMED trade.
    # It needs an actual 15m BOS/sweep and stronger 5m participation;
    # the early scanner can still report it as EARLY for observation.
    reversal_override = (
        (bos15 or sweep15)
        and m5["volume_ratio"] >= 1.35
        and m5_trigger
    )
    return bool((m15_aligned and m5_trigger) or reversal_override)


def momentum_confirmed(m15, m5, direction):
    # V6.5 TEST: 15m is the directional momentum authority.
    # 5m is the entry trigger/pullback timeframe; requiring its MACD and
    # full EMA stack to agree simultaneously caused excessive false blocking.
    if direction == "LONG":
        return (
            m15["macd_hist"] > 0
            and m15["rsi"] >= 50
            and m5["rsi"] >= 45
        )
    return (
        m15["macd_hist"] < 0
        and m15["rsi"] <= 50
        and m5["rsi"] <= 55
    )


def room_to_structure(candidate, plan):
    candles = candidate.get("candles15", [])
    if len(candles) < 22:
        return False
    window = candles[-22:-2]
    if not window:
        return False
    entry, risk = plan["entry"], abs(plan["entry"] - plan["sl"])
    if risk <= 0:
        return False
    # TP3 is 2R in this strategy. Do not confirm a setup if a nearby
    # 15m swing level blocks the path before the TP3 distance.
    required_room = risk * 2.0
    if candidate["direction"] == "LONG":
        resistance = max(c["high"] for c in window)
        return resistance <= entry or (resistance - entry) >= required_room
    support = min(c["low"] for c in window)
    return support >= entry or (entry - support) >= required_room


def analyze_higher_timeframes(c1):
    # 1H is context only; it never decides or blocks the trade direction.
    m1 = market_metrics(c1)
    long_score = 3 * (m1["trend"] == "LONG") + int(m1["rsi"] > 50) + int(m1["macd_hist"] > 0)
    short_score = 3 * (m1["trend"] == "SHORT") + int(m1["rsi"] < 50) + int(m1["macd_hist"] < 0)
    direction = m1["trend"] if m1["trend"] in ("LONG", "SHORT") else "NEUTRAL"
    return direction, long_score, short_score, m1


def score_stage2(direction, long_score, short_score, candles):
    metrics = market_metrics(candles)
    structure, sweep, bos = structure_signal(candles)
    score = long_score if direction == "LONG" else short_score
    score += 2 if structure == direction else 0
    score += int(sweep) + int(metrics["trend"] == direction)
    if direction == "LONG" and metrics["rsi"] >= 48:
        score += 1
    elif direction == "SHORT" and metrics["rsi"] <= 52:
        score += 1
    return score, metrics, structure, sweep, bos


def make_trade_plan(direction, entry, current_atr, recent_candles):
    if entry <= 0 or current_atr <= 0:
        return None
    risk_distance = current_atr * ATR_SL_MULTIPLIER
    if direction == "LONG":
        recent_low = min(c["low"] for c in recent_candles[-8:])
        stop = min(entry - risk_distance, recent_low - current_atr * 0.10)
        risk = entry - stop
        targets = [entry + risk * x for x in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)]
    else:
        recent_high = max(c["high"] for c in recent_candles[-8:])
        stop = max(entry + risk_distance, recent_high + current_atr * 0.10)
        risk = stop - entry
        targets = [entry - risk * x for x in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)]
    if risk <= 0:
        return None
    stop_atr = risk / current_atr
    # Evaluate the minimum required RR at TP3 (2R), not the distant TP6 (4R).
    rr = abs(targets[2] - entry) / risk
    if stop_atr > MAX_STOP_ATR or rr < MIN_RR:
        return None
    return {"entry": entry, "sl": stop, "tp": targets, "risk_atr": stop_atr, "rr": rr}


def analyze_symbol(symbol, ticker):
    price = ticker_price(ticker)
    if price <= 0:
        return None
    c1 = get_candles(symbol, "1h")
    time.sleep(REQUEST_PAUSE)
    if not c1:
        return None
    direction, ls, ss, m1 = analyze_higher_timeframes(c1)
    # Keep neutral 1H context candidates so 15m can establish the actual side.
    score = max(ls, ss, 4)
    return {
        "symbol": symbol, "direction": direction, "price": price,
        "long_score": ls, "short_score": ss, "score": score, "m1": m1,
    }


def analyze_stage2(candidate):
    candles = get_candles(candidate["symbol"], "15m")
    time.sleep(REQUEST_PAUSE)
    if not candles:
        return None
    metrics = market_metrics(candles)
    # 15m is the directional authority; 1H remains context only.
    direction = metrics["trend"] if metrics["trend"] in ("LONG", "SHORT") else candidate["direction"]
    if direction not in ("LONG", "SHORT"):
        return None
    candidate["direction"] = direction
    score, metrics, structure, sweep, bos = score_stage2(
        direction, candidate["long_score"], candidate["short_score"], candles
    )
    if score < MIN_SCORE_WATCH:
        return None
    micro15 = micro_breakout_signal(candles, candidate["direction"], MIN_VOLUME_RATIO)
    candidate.update({
        "score": score, "m15": metrics, "structure": structure, "sweep": sweep,
        "bos15": bos, "micro15": micro15, "candles15": candles,
    })
    return candidate


def five_minute_trigger(candles, metrics, direction):
    """Require a decisive closed 5m candle; EMA9 cross alone is never a trigger."""
    if len(candles) < 3 or not metrics.get("ema9"):
        return False
    last = candles[-1]
    previous = candles[-2]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    if body_ratio < TRIGGER_MIN_BODY_RATIO:
        return False
    if direction == "SHORT":
        decisive_close = close_position <= TRIGGER_CLOSE_EDGE
        structure_break = last["close"] < previous["low"]
        ema_reclaim_loss = last["close"] < metrics["ema9"] and last["close"] < last["open"]
        return last["close"] < last["open"] and decisive_close and (structure_break or ema_reclaim_loss)
    decisive_close = close_position >= 1.0 - TRIGGER_CLOSE_EDGE
    structure_break = last["close"] > previous["high"]
    ema_reclaim_loss = last["close"] > metrics["ema9"] and last["close"] > last["open"]
    return last["close"] > last["open"] and decisive_close and (structure_break or ema_reclaim_loss)


def candidate_quality(item):
    direction = item["direction"]
    score = item["score"]
    score += 2 if item.get("bos15") else 0
    score += 1 if item.get("sweep") else 0
    m15 = item.get("m15", {})
    if m15.get("volume_ratio", 0) >= 1.5:
        score += 2
    elif m15.get("volume_ratio", 0) >= MIN_VOLUME_RATIO:
        score += 1
    rsi15 = m15.get("rsi", 50)
    if direction == "SHORT" and 30 <= rsi15 <= 65:
        score += 1
    elif direction == "LONG" and 35 <= rsi15 <= 70:
        score += 1
    if item.get("m1", {}).get("trend") == direction:
        score += 1
    return score


def first_candle_signal(candles):
    """Return LONG/SHORT only for a closed first reversal candle after an opposing sequence."""
    if len(candles) < 30:
        return "NONE"
    m = market_metrics(candles)
    if not m["ema9"] or not m["atr"]:
        return "NONE"
    last, previous = candles[-1], candles[-2]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    previous_three = candles[-4:-1]
    bearish_before = sum(1 for c in previous_three if c["close"] < c["open"])
    bullish_before = sum(1 for c in previous_three if c["close"] > c["open"])
    prev_ema9 = ema([c["close"] for c in candles[:-1]], 9)
    if not prev_ema9:
        return "NONE"
    long_cross = previous["close"] <= prev_ema9 and last["close"] > m["ema9"]
    short_cross = previous["close"] >= prev_ema9 and last["close"] < m["ema9"]
    if (
        last["close"] > last["open"] and bearish_before >= 2
        and body_ratio >= 0.45 and close_position >= 0.68
        and (last["close"] > previous["high"] or long_cross)
        and 28.0 <= m["rsi"] <= 68.0
    ):
        return "LONG"
    if (
        last["close"] < last["open"] and bullish_before >= 2
        and body_ratio >= 0.45 and close_position <= 0.32
        and (last["close"] < previous["low"] or short_cross)
        and 32.0 <= m["rsi"] <= 72.0
    ):
        return "SHORT"
    return "NONE"


def analyze_stage3(candidate):
    direction = candidate["direction"]
    candles = get_candles(candidate["symbol"], "5m")
    time.sleep(REQUEST_PAUSE)
    if not candles:
        candidate["status"] = "WATCH"
        candidate["rejection_reason"] = "NO_5M_CANDLES"
        return candidate

    m5 = market_metrics(candles)
    structure5, sweep5, bos5 = structure_signal(candles)
    near_pullback = pullback_ok(m5, direction, candles)
    trigger5 = five_minute_trigger(candles, m5, direction)
    continuation5 = momentum_continuation_ok(candles, m5, direction)
    score = candidate["score"]
    rsi_penalty = rsi_exhaustion_penalty(candidate["m1"], candidate["m15"], direction)
    score = max(0, score - rsi_penalty)
    micro5 = micro_breakout_signal(candles, direction, MIN_VOLUME_RATIO)
    first_candle15 = first_candle_signal(candidate["candles15"])
    score += 1 if first_candle15 == direction else 0
    score += 1 if structure5 == direction else 0
    score += int(sweep5) + int(m5["trend"] == direction) + int(near_pullback) + int(trigger5)
    score += 1 if candidate.get("micro15") else 0
    score += 1 if continuation5 else 0
    candidate.update({
        "m5": m5, "score": score, "bos5": bos5, "trigger5": trigger5,
        "micro5": micro5, "continuation5": continuation5,
    })

    # 1H is informational context only; it is deliberately not a hard entry gate.
    higher_aligned = True
    volume_impulse_ok = (
        impulse_volume_ok(candidate["candles15"], direction)
        or impulse_volume_ok(candles, direction)
    )
    volume_ok = (
        candidate["m15"]["volume_ratio"] >= CONFIRM_MIN_VOLUME_RATIO
        or m5["volume_ratio"] >= CONFIRM_MIN_VOLUME_RATIO
        or volume_impulse_ok
    )
    candidate["volume_impulse_ok"] = volume_impulse_ok

    live_diff = abs(candidate["price"] - m5["price"]) / m5["price"] * 100 if m5["price"] > 0 else 999
    live_ok = live_diff <= MAX_LIVE_DIFF
    momentum_ok = momentum_confirmed(candidate["m15"], m5, direction)
    first_impulse5 = first_impulse_quality(candles, direction, min_volume_ratio=0.90)
    first_impulse_path = bool(
        first_impulse5
        and candidate["m15"]["trend"] == direction
        and ((direction == "LONG" and candidate["m15"]["rsi"] >= 48)
             or (direction == "SHORT" and candidate["m15"]["rsi"] <= 52))
    )
    directional_context_ok = confirmed_directional_context_ok(
        candidate["m15"], m5, candles, direction,
        bos15=bool(candidate.get("bos15")),
        sweep15=bool(candidate.get("sweep")),
    ) or first_impulse_path

    # Keep the 15m structure gate strict and directional.
    # The 15m directional trend confirms the first 5m impulse; waiting for 15m BOS
    # alone can delay entry until much of the move has already happened.
    structure_ok = (
        candidate["m15"]["trend"] == direction
        and ((direction == "LONG" and candidate["m15"]["price"] > candidate["m15"]["ema21"]
              and candidate["m15"]["ema9"] > candidate["m15"]["ema21"])
             or (direction == "SHORT" and candidate["m15"]["price"] < candidate["m15"]["ema21"]
                 and candidate["m15"]["ema9"] < candidate["m15"]["ema21"]))
    )
    late_ok = late_entry_ok(candidate["m15"], m5, direction)
    # 1H RSI is included in scoring/context, not used as a hard veto.
    htf_rsi_ok = True

    # Two distinct entry paths; neither bypasses the common quality/risk gates.
    pullback_path = near_pullback and trigger5
    continuation_path = continuation5
    if first_impulse_path:
        score += min(2, first_impulse5["score"] // 3)
        candidate["score"] = score
    first_candle_path = first_candle15 == direction and (trigger5 or micro5)
    entry_path = (
        "PULLBACK" if pullback_path
        else "MOMENTUM_CONTINUATION" if continuation_path
        else "FIRST_IMPULSE_5M" if first_impulse_path
        else "FIRST_CANDLE_15M" if first_candle_path
        else "NONE"
    )
    trigger_ok = pullback_path or continuation_path or first_impulse_path or first_candle_path
    candidate["first_candle15"] = first_candle15
    candidate["first_candle_path"] = first_candle_path
    candidate["first_impulse5"] = first_impulse5 or {}
    candidate["first_impulse_path"] = first_impulse_path
    candidate["entry_path"] = entry_path
    candidate["pullback_path"] = pullback_path
    candidate["continuation_path"] = continuation_path
    candidate["trigger_ok"] = trigger_ok
    candidate["structure_ok"] = structure_ok

    confirmed = (
        score >= MIN_SCORE_TRADE and higher_aligned and htf_rsi_ok and structure_ok
        and trigger_ok and momentum_ok and directional_context_ok
        and late_ok and volume_ok and live_ok
    )

    if confirmed:
        plan = make_trade_plan(direction, m5["price"], m5["atr"], candles)
        if plan and room_to_structure(candidate, plan):
            candidate["status"] = "CONFIRMED"
            candidate["plan"] = plan
            candidate["rejection_reason"] = ""
            return candidate
        candidate["rejection_reason"] = "RISK_OR_NEARBY_STRUCTURE"
    else:
        failed = []
        if not higher_aligned: failed.append("HTF")
        if not htf_rsi_ok: failed.append("HTF_RSI_EXHAUSTION")
        if not structure_ok: failed.append("15M_STRUCTURE")
        if not trigger_ok:
            failed.append("PULLBACK_AND_CONTINUATION")
        if not momentum_ok: failed.append("RSI_MACD_TREND")
        if not directional_context_ok: failed.append("15M_DIRECTION_5M_TRIGGER")
        if not late_ok: failed.append("LATE_ENTRY")
        if not volume_ok: failed.append("VOLUME")
        if not live_ok: failed.append("LIVE_PRICE_GAP")
        candidate["rejection_reason"] = ",".join(failed) or "SCORE"

    # Never send a late WATCH after the directional leg is already exhausted.
    # Such a candidate is rejected rather than advertised as an actionable setup.
    if not late_ok:
        candidate["status"] = "REJECTED_LATE"
        candidate["rejection_reason"] = (
            candidate.get("rejection_reason", "") + ",LATE_EXHAUSTED_MOVE"
        ).strip(",")
        candidate["live_diff"] = live_diff
        return candidate

    candidate["status"] = "WATCH"
    candidate["live_diff"] = live_diff
    return candidate


def classify_early_move(direction, m15):
    """Label an early 5m move as corrective, trend-aligned, or an early reversal attempt."""
    trend = str(m15.get("trend", "NEUTRAL")).upper()
    rsi15 = as_float(m15.get("rsi"), 50.0)
    macd15 = as_float(m15.get("macd_hist"), 0.0)

    if direction == "LONG":
        if trend == "SHORT" or (trend == "NEUTRAL" and (rsi15 < 48.0 or macd15 < 0)):
            return {
                "setup_type": "CORRECTIVE_LONG",
                "setup_label": "LONG اصلاحی در روند نزولی/زمینه ضعیف",
                "context_note": "این حرکت می‌تواند پولبک صعودی باشد و هنوز تغییر روند تأیید نشده است.",
            }
        if trend == "LONG":
            return {
                "setup_type": "TREND_LONG",
                "setup_label": "LONG هم‌جهت با روند صعودی",
                "context_note": "حرکت اولیه با روند زمینه هم‌جهت است.",
            }
        return {
            "setup_type": "EARLY_REVERSAL_LONG",
            "setup_label": "تلاش اولیه برای برگشت صعودی",
            "context_note": "روند زمینه خنثی است؛ شکست ساختار برای تأیید برگشت لازم است.",
        }

    if trend == "LONG" or (trend == "NEUTRAL" and (rsi15 > 52.0 or macd15 > 0)):
        return {
            "setup_type": "CORRECTIVE_SHORT",
            "setup_label": "SHORT اصلاحی در روند صعودی/زمینه قوی",
            "context_note": "این حرکت می‌تواند پولبک نزولی باشد و هنوز تغییر روند تأیید نشده است.",
        }
    if trend == "SHORT":
        return {
            "setup_type": "TREND_SHORT",
            "setup_label": "SHORT هم‌جهت با روند نزولی",
            "context_note": "حرکت اولیه با روند زمینه هم‌جهت است.",
        }
    return {
        "setup_type": "EARLY_REVERSAL_SHORT",
        "setup_label": "تلاش اولیه برای برگشت نزولی",
        "context_note": "روند زمینه خنثی است؛ شکست ساختار برای تأیید برگشت لازم است.",
    }


def first_impulse_quality(candles, direction, min_volume_ratio=0.90):
    """
    Early first-wave detector: impulse candle + fresh micro structure/sweep +
    participation + improving momentum. It scores the first closed candle,
    instead of waiting for several same-direction candles.
    """
    if len(candles) < 35:
        return None
    metrics = market_metrics(candles)
    previous_metrics = market_metrics(candles[:-1])
    if metrics["atr"] <= 0 or not metrics["ema9"]:
        return None

    last = candles[-1]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    prior = candles[-7:-1]
    prior3 = candles[-4:-1]
    if len(prior) < 6:
        return None

    prior_high = max(c["high"] for c in prior)
    prior_low = min(c["low"] for c in prior)
    if direction == "LONG":
        impulse = last["close"] > last["open"] and body_ratio >= 0.38 and close_position >= 0.64
        micro_break = last["close"] > max(c["high"] for c in prior3)
        sweep_reclaim = last["low"] < prior_low and last["close"] > prior_low
        rsi_improving = metrics["rsi"] >= 48 and metrics["rsi"] >= previous_metrics["rsi"] + 1.0
        macd_improving = metrics["macd_hist"] > previous_metrics["macd_hist"]
        ema_turn = metrics["ema9"] > previous_metrics["ema9"] and last["close"] > metrics["ema9"]
    else:
        impulse = last["close"] < last["open"] and body_ratio >= 0.38 and close_position <= 0.36
        micro_break = last["close"] < min(c["low"] for c in prior3)
        sweep_reclaim = last["high"] > prior_high and last["close"] < prior_high
        rsi_improving = metrics["rsi"] <= 52 and metrics["rsi"] <= previous_metrics["rsi"] - 1.0
        macd_improving = metrics["macd_hist"] < previous_metrics["macd_hist"]
        ema_turn = metrics["ema9"] < previous_metrics["ema9"] and last["close"] < metrics["ema9"]

    if not impulse or not (micro_break or sweep_reclaim):
        return None

    volume_ok = metrics["volume_ratio"] >= min_volume_ratio or impulse_volume_ok(candles, direction)
    if not volume_ok:
        return None

    recent_ranges = [c["high"] - c["low"] for c in candles[-4:-1]]
    base_ranges = [c["high"] - c["low"] for c in candles[-14:-4]]
    recent_avg = sum(recent_ranges) / max(len(recent_ranges), 1)
    base_avg = sum(base_ranges) / max(len(base_ranges), 1)
    compression = base_avg > 0 and recent_avg <= base_avg * 0.95

    score = 0
    score += 1 if body_ratio >= 0.48 else 0
    score += 1 if (micro_break or sweep_reclaim) else 0
    score += 1 if metrics["volume_ratio"] >= 1.15 or impulse_volume_ok(candles, direction) else 0
    score += 1 if rsi_improving else 0
    score += 1 if macd_improving else 0
    score += 1 if ema_turn else 0
    score += 1 if compression else 0
    if score < 4:
        return None

    return {
        "score": score, "body_ratio": body_ratio,
        "volume_ratio": metrics["volume_ratio"],
        "micro_break": micro_break, "sweep_reclaim": sweep_reclaim,
        "compression": compression, "rsi_improving": rsi_improving,
        "macd_improving": macd_improving, "ema_turn": ema_turn,
    }


def detect_early_5m(candles5, candles15, live_price):
    """Detect a genuinely early 5m move with volume, micro-structure and exhaustion checks."""
    if len(candles5) < 35 or live_price <= 0:
        return None
    m5 = market_metrics(candles5)
    if not m5.get("atr") or not m5.get("ema9"):
        return None
    m15 = market_metrics(candles15) if candles15 and len(candles15) >= 30 else {
        "trend": "NEUTRAL", "rsi": 50.0, "volume_ratio": 0.0, "macd_hist": 0.0
    }
    last, previous = candles5[-1], candles5[-2]
    prior3 = candles5[-4:-1]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick_ratio = upper_wick / candle_range
    lower_wick_ratio = lower_wick / candle_range

    live_gap_atr = abs(live_price - last["close"]) / m5["atr"]
    candle_size_atr = candle_range / m5["atr"]
    ema21_distance_atr = abs(last["close"] - m5["ema21"]) / m5["atr"] if m5.get("ema21") else 0.0
    if live_gap_atr > 0.60 or candle_size_atr > 2.40 or ema21_distance_atr > 1.80:
        return None

    bullish_before = sum(1 for c in prior3 if c["close"] > c["open"])
    bearish_before = sum(1 for c in prior3 if c["close"] < c["open"])
    one_bar_short_break = last["close"] < previous["low"]
    one_bar_long_break = last["close"] > previous["high"]
    micro_short_break = micro_breakout_signal(candles5, "SHORT")
    micro_long_break = micro_breakout_signal(candles5, "LONG")
    short_structure_break = one_bar_short_break or micro_short_break
    long_structure_break = one_bar_long_break or micro_long_break

    first_bearish = (
        last["close"] < last["open"] and bullish_before >= 2
        and body_ratio >= 0.45 and close_position <= 0.38
        and short_structure_break
    )
    first_bullish = (
        last["close"] > last["open"] and bearish_before >= 2
        and body_ratio >= 0.45 and close_position >= 0.62
        and long_structure_break
    )
    recent_high = max(c["high"] for c in candles5[-7:-1])
    recent_low = min(c["low"] for c in candles5[-7:-1])
    short_rejection = (
        last["high"] > recent_high and last["close"] < recent_high
        and last["close"] < last["open"] and upper_wick_ratio >= 0.30
        and close_position <= 0.45
    )
    long_rejection = (
        last["low"] < recent_low and last["close"] > recent_low
        and last["close"] > last["open"] and lower_wick_ratio >= 0.30
        and close_position >= 0.55
    )

    # Earlier swing-failure detector: catch the first rollover candle near a
    # local extreme before a multi-candle BOS has already happened.
    prev5_metrics = market_metrics(candles5[:-1])
    prior_high = max(c["high"] for c in candles5[-7:-1])
    prior_low = min(c["low"] for c in candles5[-7:-1])
    short_swing_failure = (
        last["close"] < last["open"]
        and last["high"] >= prior_high * 0.999
        and last["close"] < prior_high
        and upper_wick_ratio >= 0.20
        and close_position <= 0.58
        and prev5_metrics["rsi"] - m5["rsi"] >= 1.0
        and m5["macd_hist"] < prev5_metrics["macd_hist"]
        and (m5["volume_ratio"] >= 0.80 or impulse_volume_ok(candles5, "SHORT"))
    )
    long_swing_failure = (
        last["close"] > last["open"]
        and last["low"] <= prior_low * 1.001
        and last["close"] > prior_low
        and lower_wick_ratio >= 0.20
        and close_position >= 0.42
        and m5["rsi"] - prev5_metrics["rsi"] >= 1.0
        and m5["macd_hist"] > prev5_metrics["macd_hist"]
        and (m5["volume_ratio"] >= 0.80 or impulse_volume_ok(candles5, "LONG"))
    )

    # PRE-BREAKDOWN / PRE-BREAKOUT heads-up:
    # identify the first loss of momentum near a local extreme, before waiting
    # for a full micro-BOS. This is an early warning trigger, not a prediction
    # that the exact swing high/low has been found.
    local_high = max(c["high"] for c in candles5[-12:-1])
    local_low = min(c["low"] for c in candles5[-12:-1])
    bearish_last3 = sum(1 for x in candles5[-3:] if x["close"] < x["open"])
    bullish_last3 = sum(1 for x in candles5[-3:] if x["close"] > x["open"])
    short_pre_breakdown = (
        last["close"] < last["open"]
        and last["close"] < previous["close"]
        and last["high"] >= local_high - m5["atr"] * 0.35
        and last["close"] < m5["ema9"]
        and m5["rsi"] <= prev5_metrics["rsi"] - 1.0
        and m5["macd_hist"] < prev5_metrics["macd_hist"]
        and 38.0 < m5["rsi"] < 68.0
        and bearish_last3 <= 2
        and (m5["volume_ratio"] >= 0.65 or impulse_volume_ok(candles5, "SHORT"))
        and m15.get("rsi", 50.0) > 33.0
    )
    long_pre_breakout = (
        last["close"] > last["open"]
        and last["close"] > previous["close"]
        and last["low"] <= local_low + m5["atr"] * 0.35
        and last["close"] > m5["ema9"]
        and m5["rsi"] >= prev5_metrics["rsi"] + 1.0
        and m5["macd_hist"] > prev5_metrics["macd_hist"]
        and 32.0 < m5["rsi"] < 62.0
        and bullish_last3 <= 2
        and (m5["volume_ratio"] >= 0.65 or impulse_volume_ok(candles5, "LONG"))
        and m15.get("rsi", 50.0) < 67.0
    )

    # Predict the first directional impulse candle using 7 evidence points.
    # This is evaluated before legacy triggers so the alert can fire on the first
    # closed impulse candle, not after a multi-candle trend is already mature.
    early_short = first_impulse_quality(candles5, "SHORT")
    early_long = first_impulse_quality(candles5, "LONG")
    direction = trigger = None
    predictive = None
    if early_short and (not early_long or early_short["score"] >= early_long["score"]):
        direction, trigger, predictive = "SHORT", "FIRST_IMPULSE_5M_PREDICTIVE", early_short
    elif early_long:
        direction, trigger, predictive = "LONG", "FIRST_IMPULSE_5M_PREDICTIVE", early_long

    if not direction and short_pre_breakdown:
        direction, trigger = "SHORT", "5M_PRE_BREAKDOWN_ROLLOVER"
    elif not direction and long_pre_breakout:
        direction, trigger = "LONG", "5M_PRE_BREAKOUT_TURN"
    elif not direction and short_rejection:
        direction, trigger = "SHORT", "5M_RESISTANCE_REJECTION"
    elif not direction and long_rejection:
        direction, trigger = "LONG", "5M_SUPPORT_RECLAIM"
    elif not direction and short_swing_failure:
        direction, trigger = "SHORT", "5M_EARLY_SWING_FAILURE"
    elif not direction and long_swing_failure:
        direction, trigger = "LONG", "5M_EARLY_SWING_FAILURE"
    elif not direction and first_bearish:
        direction, trigger = "SHORT", "FIRST_BEARISH_5M_CANDLE"
    elif not direction and first_bullish:
        direction, trigger = "LONG", "FIRST_BULLISH_5M_CANDLE"
    elif not direction and micro_short_break and last["close"] < last["open"] and body_ratio >= 0.45 and close_position <= 0.38:
        direction, trigger = "SHORT", "5M_MICRO_BOS"
    elif not direction and micro_long_break and last["close"] > last["open"] and body_ratio >= 0.45 and close_position >= 0.62:
        direction, trigger = "LONG", "5M_MICRO_BOS"

    if not direction:
        return None

    # A single EMA9 cross or a candle closing near its extreme is not enough.
    # Require meaningful participation and an observable price/structure break.
    volume_floor = 0.65 if trigger in ("5M_PRE_BREAKDOWN_ROLLOVER", "5M_PRE_BREAKOUT_TURN") else (0.90 if predictive else 1.05)
    volume_ok = m5["volume_ratio"] >= volume_floor or impulse_volume_ok(candles5, direction)
    if not volume_ok:
        return None
    micro_bos = short_structure_break if direction == "SHORT" else long_structure_break
    structure_direction, _sweep, structure_bos = structure_signal(candles5)
    predictive_sweep = bool(predictive and predictive.get("sweep_reclaim"))
    fresh_bos = micro_bos or predictive_sweep or (structure_direction == direction and structure_bos) or trigger == "5M_EARLY_SWING_FAILURE"
    early_turn_trigger = trigger in ("5M_PRE_BREAKDOWN_ROLLOVER", "5M_PRE_BREAKOUT_TURN")
    if not fresh_bos and not early_turn_trigger and trigger not in ("5M_RESISTANCE_REJECTION", "5M_SUPPORT_RECLAIM", "5M_EARLY_SWING_FAILURE"):
        return None

    setup = classify_early_move(direction, m15)
    entry_confirmation = "5M_STRUCTURE_AND_VOLUME"
    if setup["setup_type"] in ("CORRECTIVE_LONG", "CORRECTIVE_SHORT"):
        if not fresh_bos and not early_turn_trigger:
            return None
        entry_confirmation = "CORRECTIVE_MICRO_BOS_CONFIRMED"
    elif setup["setup_type"].startswith("EARLY_REVERSAL_"):
        if not fresh_bos and not early_turn_trigger and trigger not in ("5M_RESISTANCE_REJECTION", "5M_SUPPORT_RECLAIM", "5M_EARLY_SWING_FAILURE"):
            return None
        entry_confirmation = "EARLY_REVERSAL_STRUCTURE_CONFIRMED"

    # Do not chase a mature 15m move. A very fresh BOS with strong volume is
    # the only exception when RSI is already at an exhaustion extreme.
    rsi15 = as_float(m15.get("rsi"), 50.0)
    if direction == "SHORT":
        # At RSI <= 33 the 15m leg is already too stretched for a fresh SHORT.
        if rsi15 <= 33.0:
            return None
        if rsi15 <= 38.0 and not fresh_bos:
            return None
    else:
        # Symmetric protection against buying an already overbought 15m leg.
        if rsi15 >= 67.0:
            return None
        if rsi15 >= 62.0 and not fresh_bos:
            return None

    entry = live_price
    recent = candles5[-4:]
    buffer = max(m5["atr"] * 0.15, entry * 0.0003)
    min_risk = m5["atr"] * 0.65
    if direction == "SHORT":
        structural_stop = max(c["high"] for c in recent) + buffer
        stop = max(structural_stop, entry + min_risk)
    else:
        structural_stop = min(c["low"] for c in recent) - buffer
        stop = min(structural_stop, entry - min_risk)
    risk = abs(stop - entry)
    risk_atr = risk / m5["atr"]
    if risk <= 0 or risk_atr > 2.00:
        return None
    if (direction == "SHORT" and stop <= entry) or (direction == "LONG" and stop >= entry):
        return None

    r_multiples = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
    targets = [entry - risk * r if direction == "SHORT" else entry + risk * r for r in r_multiples]
    score = 5
    if predictive:
        score = max(score, predictive["score"] + 3)
    score += 2 if trigger in ("5M_RESISTANCE_REJECTION", "5M_SUPPORT_RECLAIM") else 0
    score += 1 if body_ratio >= 0.55 else 0
    score += 1 if m5["volume_ratio"] >= 1.50 else 0
    score += 1 if m15.get("trend") == direction else 0
    score += 1 if fresh_bos else 0
    return {
        "symbol": "", "direction": direction, "price": entry, "entry": entry,
        "sl": stop, "tp": targets, "score": score, "m5": m5, "m15": m15,
        "body_ratio": body_ratio, "trigger": trigger, "risk_atr": risk_atr,
        "setup_type": setup["setup_type"], "setup_label": setup["setup_label"],
        "context_note": setup["context_note"], "entry_confirmation": entry_confirmation,
        "micro_bos": fresh_bos, "volume_confirmed": volume_ok,
        "predictive_score": predictive["score"] if predictive else 0,
        "first_impulse_evidence": predictive or {},
    }

def early_5m_turn_confirmation_ok(candles5, direction):
    """Low-latency 5m corroboration for a fresh 15m swing turn."""
    if not candles5 or len(candles5) < 35:
        return False
    m = market_metrics(candles5)
    p = market_metrics(candles5[:-1])
    last = candles5[-1]
    prior = candles5[-7:-1]
    if not prior or m["atr"] <= 0:
        return False
    rng = max(last["high"] - last["low"], 1e-12)
    close_pos = (last["close"] - last["low"]) / rng
    lower_wick = (min(last["open"], last["close"]) - last["low"]) / rng
    upper_wick = (last["high"] - max(last["open"], last["close"])) / rng
    recent_pivots = candles5[-5:-1]
    if direction == "LONG":
        pivot = min(x["low"] for x in recent_pivots)
        bars_since_pivot = len(recent_pivots) - 1 - min(range(len(recent_pivots)), key=lambda i: recent_pivots[i]["low"])
        fresh_turn = (
            1 <= bars_since_pivot <= 3
            and last["close"] >= pivot
            and (last["close"] - pivot) / m["atr"] <= 1.50
            and last["close"] > last["open"]
            and (last["close"] > candles5[-2]["high"] or (last["close"] > candles5[-2]["close"] and close_pos >= 0.60))
        )
        turn = (
            fresh_turn and (lower_wick >= 0.10 or close_pos >= 0.60)
            and m["rsi"] >= p["rsi"] + 0.25
            and m["macd_hist"] > p["macd_hist"]
            and m["rsi"] < 65
        )
    else:
        pivot = max(x["high"] for x in recent_pivots)
        bars_since_pivot = len(recent_pivots) - 1 - max(range(len(recent_pivots)), key=lambda i: recent_pivots[i]["high"])
        fresh_turn = (
            1 <= bars_since_pivot <= 3
            and last["close"] <= pivot
            and (pivot - last["close"]) / m["atr"] <= 1.50
            and last["close"] < last["open"]
            and (last["close"] < candles5[-2]["low"] or (last["close"] < candles5[-2]["close"] and close_pos <= 0.40))
        )
        turn = (
            fresh_turn and (upper_wick >= 0.10 or close_pos <= 0.40)
            and m["rsi"] <= p["rsi"] - 0.25
            and m["macd_hist"] < p["macd_hist"]
            and m["rsi"] > 35
        )
    return bool(turn and (m["volume_ratio"] >= 0.55 or impulse_volume_ok(candles5, direction)))


def early_5m_confirmation_ok(candles5, direction):
    """Require the same qualified first 5m impulse before a 15m early alert."""
    return first_impulse_quality(candles5, direction, min_volume_ratio=0.90) is not None


def detect_early_15m(candles, live_price, candles5=None, require_5m=False):
    """
    Detect an early 15m move, but never treat EMA9 cross or candle color alone
    as an entry. A candidate must have structure, participation and 5m proof.
    """
    if len(candles) < 35 or live_price <= 0:
        return None
    m = market_metrics(candles)
    if not m["atr"] or not m["ema9"] or not m["ema21"]:
        return None

    last, previous = candles[-1], candles[-2]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick_ratio = upper_wick / candle_range
    lower_wick_ratio = lower_wick / candle_range
    prev_ema9 = ema([c["close"] for c in candles[:-1]], 9)
    if not prev_ema9:
        return None

    live_gap_atr = abs(live_price - last["close"]) / m["atr"]
    ema21_distance_atr = abs(last["close"] - m["ema21"]) / m["atr"]
    candle_size_atr = candle_range / m["atr"]
    if live_gap_atr > 0.60 or ema21_distance_atr > 1.65 or candle_size_atr > 2.20:
        return None

    prior = candles[-7:-1]
    prior_high = max(c["high"] for c in prior)
    prior_low = min(c["low"] for c in prior)
    short_break = last["close"] < prior_low
    long_break = last["close"] > prior_high
    short_cross = previous["close"] >= prev_ema9 and last["close"] < m["ema9"]
    long_cross = previous["close"] <= prev_ema9 and last["close"] > m["ema9"]

    previous_three = candles[-4:-1]
    bearish_before = sum(1 for c in previous_three if c["close"] < c["open"])
    bullish_before = sum(1 for c in previous_three if c["close"] > c["open"])
    first_bullish = (
        last["close"] > last["open"] and bearish_before >= 2
        and body_ratio >= 0.50 and close_position >= 0.68
        and last["close"] > previous["high"]
    )
    first_bearish = (
        last["close"] < last["open"] and bullish_before >= 2
        and body_ratio >= 0.50 and close_position <= 0.32
        and last["close"] < previous["low"]
    )

    short_reversal = (
        last["high"] > prior_high and last["close"] < prior_high
        and last["close"] < last["open"] and upper_wick_ratio >= 0.30
        and close_position <= 0.50
    )
    long_reversal = (
        last["low"] < prior_low and last["close"] > prior_low
        and last["close"] > last["open"] and lower_wick_ratio >= 0.30
        and close_position >= 0.50
    )

    # Detect a fresh swing turn at the base/top before a multi-candle
    # range break. This is deliberately limited to non-extended momentum.
    prev_metrics = market_metrics(candles[:-1])
    turn_prior = candles[-7:-1]
    turn_low = min(x["low"] for x in turn_prior)
    turn_high = max(x["high"] for x in turn_prior)
    # Find a just-formed local pivot among the last few CLOSED candles.
    # The confirmation candle may arrive 1-3 bars after the actual low/high;
    # it does NOT need to sweep the pivot again (the old late-entry flaw).
    pivot_window = candles[-5:-1]
    pivot_low_idx = min(range(len(pivot_window)), key=lambda i: pivot_window[i]["low"])
    pivot_high_idx = max(range(len(pivot_window)), key=lambda i: pivot_window[i]["high"])
    pivot_low = pivot_window[pivot_low_idx]["low"]
    pivot_high = pivot_window[pivot_high_idx]["high"]
    bars_after_low = len(pivot_window) - 1 - pivot_low_idx
    bars_after_high = len(pivot_window) - 1 - pivot_high_idx
    near_fresh_low = (
        1 <= bars_after_low <= 3
        and (last["close"] - pivot_low) / m["atr"] <= 1.25
        and last["close"] >= pivot_low
    )
    near_fresh_high = (
        1 <= bars_after_high <= 3
        and (pivot_high - last["close"]) / m["atr"] <= 1.25
        and last["close"] <= pivot_high
    )
    first_bullish_shift = (
        last["close"] > last["open"]
        and (last["close"] > previous["high"] or
             (last["close"] > previous["close"] and close_position >= 0.62))
    )
    first_bearish_shift = (
        last["close"] < last["open"]
        and (last["close"] < previous["low"] or
             (last["close"] < previous["close"] and close_position <= 0.38))
    )
    long_base_turn = (
        near_fresh_low and first_bullish_shift
        and (lower_wick_ratio >= 0.10 or close_position >= 0.62)
        and m["rsi"] >= prev_metrics["rsi"] + 0.25
        and m["macd_hist"] > prev_metrics["macd_hist"]
        and 32.0 <= m["rsi"] <= 60.0
        and abs(last["close"] - m["ema21"]) / m["atr"] <= 1.50
        and (m["volume_ratio"] >= 0.50 or impulse_volume_ok(candles, "LONG"))
    )
    short_top_turn = (
        near_fresh_high and first_bearish_shift
        and (upper_wick_ratio >= 0.10 or close_position <= 0.38)
        and m["rsi"] <= prev_metrics["rsi"] - 0.25
        and m["macd_hist"] < prev_metrics["macd_hist"]
        and 40.0 <= m["rsi"] <= 68.0
        and abs(last["close"] - m["ema21"]) / m["atr"] <= 1.50
        and (m["volume_ratio"] >= 0.50 or impulse_volume_ok(candles, "SHORT"))
    )

    # Detect the first 15m impulse candle from structure, volume and
    # momentum acceleration. The separate 5m gate below must also agree.
    early_short = first_impulse_quality(candles, "SHORT")
    early_long = first_impulse_quality(candles, "LONG")
    direction = trigger = None
    predictive = None
    if long_base_turn:
        direction, trigger = "LONG", "15M_EARLY_BASE_TURN"
    elif short_top_turn:
        direction, trigger = "SHORT", "15M_EARLY_TOP_TURN"
    elif early_short and (not early_long or early_short["score"] >= early_long["score"]):
        direction, trigger, predictive = "SHORT", "FIRST_IMPULSE_15M_PREDICTIVE", early_short
    elif early_long:
        direction, trigger, predictive = "LONG", "FIRST_IMPULSE_15M_PREDICTIVE", early_long

    if not direction and short_reversal:
        direction, trigger = "SHORT", "15M_SWING_HIGH_REJECTION"
    elif not direction and long_reversal:
        direction, trigger = "LONG", "15M_SWING_LOW_RECLAIM"
    elif not direction and first_bearish:
        direction, trigger = "SHORT", "FIRST_BEARISH_15M_CANDLE"
    elif not direction and first_bullish:
        direction, trigger = "LONG", "FIRST_BULLISH_15M_CANDLE"
    elif not direction and short_break and last["close"] < last["open"] and body_ratio >= 0.45 and close_position <= 0.35:
        direction, trigger = "SHORT", "15M_RANGE_BREAK"
    elif not direction and long_break and last["close"] > last["open"] and body_ratio >= 0.45 and close_position >= 0.65:
        direction, trigger = "LONG", "15M_RANGE_BREAK"
    elif not direction and short_cross and last["close"] < previous["low"] and last["close"] < last["open"] and m["volume_ratio"] >= 1.25:
        direction, trigger = "SHORT", "15M_EMA9_CROSS_MICRO_BREAK"
    elif not direction and long_cross and last["close"] > previous["high"] and last["close"] > last["open"] and m["volume_ratio"] >= 1.25:
        direction, trigger = "LONG", "15M_EMA9_CROSS_MICRO_BREAK"

    if not direction:
        return None

    # Avoid chasing an already exhausted 15m leg unless a fresh BOS is backed
    # by exceptional participation.
    structure_direction, _sweep, structure_bos = structure_signal(candles)
    fresh_bos = structure_bos and structure_direction == direction
    if direction == "SHORT" and m["rsi"] <= 35.0 and not (fresh_bos and m["volume_ratio"] >= 1.50):
        return None
    if direction == "LONG" and m["rsi"] >= 65.0 and not (fresh_bos and m["volume_ratio"] >= 1.50):
        return None

    volume_floor = 0.60 if trigger in ("15M_EARLY_BASE_TURN", "15M_EARLY_TOP_TURN") else (1.10 if trigger in ("15M_RANGE_BREAK", "FIRST_BEARISH_15M_CANDLE", "FIRST_BULLISH_15M_CANDLE") else 0.95)
    volume_ok = m["volume_ratio"] >= volume_floor or impulse_volume_ok(candles, direction)
    if not volume_ok:
        return None

    if require_5m:
        five_min_ok = early_5m_confirmation_ok(candles5 or [], direction)
        if trigger in ("15M_EARLY_BASE_TURN", "15M_EARLY_TOP_TURN"):
            five_min_ok = five_min_ok or early_5m_turn_confirmation_ok(candles5 or [], direction)
        if not five_min_ok:
            return None

    entry = live_price
    recent = candles[-4:]
    buffer = max(m["atr"] * 0.15, entry * 0.0005)
    min_risk = m["atr"] * 0.65
    if direction == "SHORT":
        structural_stop = max(c["high"] for c in recent) + buffer
        stop = max(structural_stop, entry + min_risk)
    else:
        structural_stop = min(c["low"] for c in recent) - buffer
        stop = min(structural_stop, entry - min_risk)
    risk = abs(stop - entry)
    risk_atr = risk / m["atr"]
    if risk <= 0 or risk_atr > 1.80:
        return None

    targets = (
        [entry - risk, entry - risk * 1.5]
        if direction == "SHORT"
        else [entry + risk, entry + risk * 1.5]
    )
    score = 6
    if predictive:
        score = max(score, predictive["score"] + 3)
    score += 2 if trigger in ("15M_SWING_HIGH_REJECTION", "15M_SWING_LOW_RECLAIM", "15M_EARLY_BASE_TURN", "15M_EARLY_TOP_TURN") else 0
    score += 1 if m["volume_ratio"] >= 1.20 else 0
    score += 1 if body_ratio >= 0.60 or upper_wick_ratio >= 0.40 or lower_wick_ratio >= 0.40 else 0
    score += 1 if fresh_bos else 0

    return {
        "symbol": "", "direction": direction, "price": entry, "entry": entry,
        "sl": stop, "tp": targets, "score": score, "m15": m,
        "body_ratio": body_ratio, "breakout": short_break if direction == "SHORT" else long_break,
        "trigger": trigger, "risk_atr": risk_atr,
        "confirmation_5m": bool(require_5m),
        "predictive_score": predictive["score"] if predictive else 0,
        "first_impulse_evidence": predictive or {},
    }



def early_entry_message(item):
    direction_fa = "فروش SHORT" if item["direction"] == "SHORT" else "خرید LONG"
    lines = [
        "🟠 ARAD CRYPTO PRO | EARLY {} ENTRY".format(item.get("source_tf", "5M")),
        "هشدار شروع حرکت بر اساس اولین نشانه قابل‌شناسایی در کندل بسته {}".format("۵ دقیقه" if item.get("source_tf") == "5M" else "۱۵ دقیقه با تأیید ۵ دقیقه"),
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت: {}".format(direction_fa),
        "قیمت/ورود لحظه هشدار: {}".format(format_price(item["entry"])),
        "حد ضرر ساختاری: {}".format(format_price(item["sl"])),
    ]
    for i, target in enumerate(item["tp"], 1):
        lines.append("TP{}: {}".format(i, format_price(target)))
    lines += [
        "ریسک: {:.2f} ATR | تعداد تارگت‌ها: {}".format(item["risk_atr"], len(item["tp"])),
        "RSI 5m: {:.1f} | حجم: {:.2f}x".format(item["m5"]["rsi"], item["m5"]["volume_ratio"]),
        "ساختار خرد: {} | تأیید حجم: {}".format("شکست تأیید شد" if item.get("micro_bos") else "رد شد", "بله" if item.get("volume_confirmed") else "خیر"),
        "قدرت پیش‌بینی کندل اول: {}/7".format(item.get("predictive_score", 0)),
        "نشانه‌ها: شکست خرد={} | جمع‌آوری نقدینگی={} | فشردگی={} | بهبود RSI={} | بهبود MACD={} | چرخش EMA9={}".format(
            "بله" if item.get("first_impulse_evidence", {}).get("micro_break") else "خیر",
            "بله" if item.get("first_impulse_evidence", {}).get("sweep_reclaim") else "خیر",
            "بله" if item.get("first_impulse_evidence", {}).get("compression") else "خیر",
            "بله" if item.get("first_impulse_evidence", {}).get("rsi_improving") else "خیر",
            "بله" if item.get("first_impulse_evidence", {}).get("macd_improving") else "خیر",
            "بله" if item.get("first_impulse_evidence", {}).get("ema_turn") else "خیر",
        ),
        "روند زمینه 15m: {} | RSI: {:.1f}".format(item["m15"]["trend"], item["m15"]["rsi"]),
        "نوع حرکت: {}".format(item.get("setup_label", "نامشخص")),
        "تفسیر: {}".format(item.get("context_note", "زمینه حرکت نیازمند بررسی است.")),
        "تأیید ورود: {}".format(
            "شکست ساختار خرد ۵ دقیقه تأیید شد" if item.get("entry_confirmation") == "CORRECTIVE_MICRO_BOS_CONFIRMED"
            else ("محرک ۱۵ دقیقه با تأیید ۵ دقیقه" if item.get("source_tf") == "15M" else "محرک اولیه ۵ دقیقه؛ هنوز برگشت روند قطعی نیست")
        ),
        "محرک: {} | قدرت بدنه: {:.0f}%".format(item["trigger"], item["body_ratio"] * 100),
        "امتیاز شروع حرکت: {}".format(item["score"]),
        "",
        "⚠️ هشدار زودهنگام آزمایشی؛ سفارش خودکار ثبت نمی‌شود.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def format_price(value):
    if value >= 1000:
        return "{:,.4f}".format(value)
    if value >= 1:
        return "{:.5f}".format(value)
    if value >= 0.01:
        return "{:.7f}".format(value)
    return "{:.10f}".format(value)


# ---------------- TELEGRAM ----------------

def telegram_send(message):
    token = os.getenv("BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()
    if not token or not chat_id:
        log("TELEGRAM SKIP: BOT_TOKEN or CHAT_ID secret is missing")
        return False
    url = "{}/bot{}/sendMessage".format(TELEGRAM_API, token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        result = http_json(url, method="POST", payload=payload, timeout=20)
        if isinstance(result, dict) and result.get("ok") is True:
            log("TELEGRAM SENT")
            return True
        log("TELEGRAM ERROR: {}".format(str(result)[:220]))
    except Exception as exc:
        log("TELEGRAM ERROR: {}".format(str(exc)[:180]))
    return False


# Store deduplication state in the repository using the Actions GITHUB_TOKEN.
# The workflow must grant contents: write and pass GITHUB_TOKEN to this script.
def github_state_request(method, payload=None, sha=None):
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_REF_NAME", "main").strip()
    if not token or not repo:
        return None
    url = "https://api.github.com/repos/{}/contents/{}".format(
        repo, urllib.parse.quote(STATE_FILE)
    )
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if method == "GET":
        try:
            data = http_json(url + "?ref=" + urllib.parse.quote(branch), headers=headers)
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            return {"state": json.loads(content), "sha": data.get("sha")}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"state": {}, "sha": None}
            log("STATE READ ERROR: HTTP {}".format(exc.code))
            return None
        except Exception as exc:
            log("STATE READ ERROR: {}".format(str(exc)[:150]))
            return None
    if method == "PUT":
        content = base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
        body = {
            "message": "Update ARAD signal deduplication state",
            "content": content,
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        try:
            result = http_json(url, method="PUT", payload=body, headers=headers)
            return bool(isinstance(result, dict) and result.get("content"))
        except Exception as exc:
            log("STATE SAVE ERROR: {}".format(str(exc)[:180]))
            return False
    return None


def load_signal_state():
    result = github_state_request("GET")
    if result is None:
        log("WARNING: persistent anti-duplicate state unavailable")
        return {}, None, False
    return result["state"], result.get("sha"), True


def save_signal_state(state, sha):
    saved = github_state_request("PUT", payload=state, sha=sha)
    if not saved:
        log("WARNING: signal state was not saved; duplicate alerts may occur next run")
    return saved


def should_send_alert(state, item, alert_type):
    symbol = item["symbol"]
    direction = item["direction"]
    key = symbol + "|" + direction
    now = int(time.time())
    previous = state.get(key, {})
    cooldown = (EARLY_COOLDOWN_MIN if alert_type == "EARLY" else WATCH_COOLDOWN_MIN if alert_type == "WATCH" else CONFIRMED_COOLDOWN_MIN)

    # A confirmed alert is allowed immediately after a WATCH alert.
    if alert_type == "CONFIRMED" and previous.get("type") == "WATCH":
        return True
    # Avoid repeating an early entry if another alert type for the same
    # symbol/direction was sent moments earlier.
    if alert_type == "EARLY" and previous.get("type") in ("EARLY", "WATCH", "CONFIRMED"):
        if now - int(previous.get("sent_at", 0)) < EARLY_COOLDOWN_MIN * 60:
            return False
    if previous.get("type") == alert_type and now - int(previous.get("sent_at", 0)) < cooldown * 60:
        return False
    return True


def mark_alert_sent(state, item, alert_type):
    key = item["symbol"] + "|" + item["direction"]
    state[key] = {"type": alert_type, "sent_at": int(time.time())}
    # Keep the state file small.
    cutoff = int(time.time()) - 7 * 24 * 60 * 60
    return {k: v for k, v in state.items() if int(v.get("sent_at", 0)) >= cutoff}


def watch_message(item):
    m15 = item["m15"]
    lines = [
        "🟡 ARAD CRYPTO PRO | TEST WATCH",
        "هشدار زودهنگام - هنوز نقطه ورود قطعی نیست",
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت احتمالی: {}".format("خرید LONG" if item["direction"] == "LONG" else "فروش SHORT"),
        "قیمت فعلی: {}".format(format_price(item["price"])),
        "امتیاز تحلیلی: {} امتیاز".format(item["score"]),
        "1H context: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]),
        "1H: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]),
        "15M: {} | RSI {:.1f} | حجم {:.2f}x".format(m15["trend"], m15["rsi"], m15["volume_ratio"]),
        "BOS 15m: {}".format("بله" if item["bos15"] else "خیر"),
        "Sweep: {}".format("بله" if item["sweep"] else "خیر"),
        "",
        "⚠️ فقط تحت نظر بگیر؛ ورود بدون تأیید نهایی توصیه نمی‌شود.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def confirmed_message(item):
    plan = item["plan"]
    lines = [
        "🟢 ARAD CRYPTO PRO | TEST CONFIRMED",
        "سیگنال تأییدشده - بررسی ورود با مدیریت ریسک",
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت: {}".format("LONG خرید" if item["direction"] == "LONG" else "SHORT فروش"),
        "قیمت لحظه تحلیل: {}".format(format_price(item["price"])),
        "ورود پیشنهادی: {}".format(format_price(plan["entry"])),
        "حد ضرر: {}".format(format_price(plan["sl"])),
    ]
    for i, target in enumerate(plan["tp"], 1):
        lines.append("TP{}: {}".format(i, format_price(target)))
    lines += [
        "فاصله حد ضرر: {:.2f} ATR".format(plan["risk_atr"]),
        "نسبت سود به زیان تا TP3: {:.2f}".format(plan["rr"]),
        "امتیاز تحلیلی: {} امتیاز".format(item["score"]),
        "",
        "⚠️ این پیام تحلیل است، نه دستور معامله. سفارش خودکار ثبت نشده است.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def dispatch_alerts(early_items, watch_items, confirmed_items):
    # Safety lock: this version sends actionable CONFIRMED plans only.
    if TELEGRAM_CONFIRMED_ONLY:
        early_items = []
        watch_items = []
    state, sha, state_ok = load_signal_state()
    if not state_ok:
        # Fail closed: avoid flooding Telegram if deduplication cannot be checked.
        log("ALERTS PAUSED: cannot verify duplicate-alert state")
        return

    sent_any = False
    for item in early_items:
        if should_send_alert(state, item, "EARLY"):
            if telegram_send(early_entry_message(item)):
                journal_ok = save_outcome_signal(item, "EARLY")
                log("EARLY OUTCOME TRACKING: {}".format("ON" if journal_ok else "FAILED"))
                state = mark_alert_sent(state, item, "EARLY")
                sent_any = True
        else:
            log("EARLY DUPLICATE SKIPPED: {}".format(item["symbol"]))

    for item in watch_items:
        if should_send_alert(state, item, "WATCH"):
            if telegram_send(watch_message(item)):
                state = mark_alert_sent(state, item, "WATCH")
                sent_any = True
        else:
            log("WATCH DUPLICATE SKIPPED: {}".format(item["symbol"]))

    for item in confirmed_items:
        if should_send_alert(state, item, "CONFIRMED"):
            if telegram_send(confirmed_message(item)):
                journal_ok = save_outcome_signal(item, "CONFIRMED")
                log("CONFIRMED OUTCOME TRACKING: {}".format("ON" if journal_ok else "FAILED"))
                state = mark_alert_sent(state, item, "CONFIRMED")
                sent_any = True
        else:
            log("CONFIRMED DUPLICATE SKIPPED: {}".format(item["symbol"]))

    if sent_any:
        save_signal_state(state, sha)
    else:
        log("TELEGRAM: no new alerts to send")



def save_outcome_signal(item, alert_type):
    """Persist actionable alerts for later TP1/SL-first evaluation."""
    if alert_type == "EARLY":
        entry = as_float(item.get("entry"))
        stop = as_float(item.get("sl"))
        targets = item.get("tp", [])
    elif alert_type == "CONFIRMED":
        plan = item.get("plan", {})
        entry = as_float(plan.get("entry"))
        stop = as_float(plan.get("sl"))
        targets = plan.get("tp", [])
    else:
        return False

    targets = [as_float(x) for x in targets if as_float(x) > 0]
    if entry <= 0 or stop <= 0 or not targets:
        log("OUTCOME JOURNAL SKIP: invalid entry/SL/TP for {}".format(item.get("symbol")))
        return False

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_REF_NAME", "main").strip()
    if not token or not repo:
        log("OUTCOME JOURNAL ERROR: GitHub token/repository missing")
        return False

    url = "https://api.github.com/repos/{}/contents/{}".format(repo, urllib.parse.quote(OUTCOME_FILE))
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    current = {"signals": []}
    sha = None
    try:
        response = http_json(url + "?ref=" + urllib.parse.quote(branch), headers=headers)
        sha = response.get("sha")
        raw = base64.b64decode(response.get("content", "")).decode("utf-8")
        current = json.loads(raw) if raw.strip() else {"signals": []}
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            log("OUTCOME JOURNAL READ ERROR: HTTP {}".format(exc.code))
            return False
    except Exception as exc:
        log("OUTCOME JOURNAL READ ERROR: {}".format(str(exc)[:160]))
        return False

    created = int(time.time())
    symbol = str(item.get("symbol", "")).upper()
    direction = str(item.get("direction", "")).upper()
    signal_id = "{}|{}|{}|{}".format(symbol, direction, alert_type, created)
    signals = current.get("signals", []) if isinstance(current, dict) else []
    if not isinstance(signals, list):
        signals = []
    if any(x.get("id") == signal_id for x in signals if isinstance(x, dict)):
        return True

    signals.append({
        "id": signal_id, "symbol": symbol, "direction": direction,
        "alert_type": alert_type, "entry": entry, "sl": stop, "tp": targets,
        "created_at": created,
        "first_candle_time": ((created + 299) // 300) * 300,
        "status": "OPEN", "result": None, "max_tp": 0,
    })
    current = {"signals": signals[-500:]}
    body = {
        "message": "Record ARAD signal for TP1/SL outcome tracking",
        "content": base64.b64encode(json.dumps(current, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    try:
        saved = http_json(url, method="PUT", payload=body, headers=headers, timeout=25)
        ok = isinstance(saved, dict) and bool(saved.get("content"))
        log("OUTCOME JOURNAL SAVED: {} {} {}".format(symbol, direction, alert_type) if ok else "OUTCOME JOURNAL SAVE FAILED")
        return ok
    except Exception as exc:
        log("OUTCOME JOURNAL SAVE ERROR: {}".format(str(exc)[:180]))
        return False


def print_result(item):
    log("")
    log("=" * 58)
    log("{} | {} | SCORE {} POINTS".format(item["symbol"], item["direction"], item["score"]))
    log("STATUS: {}".format(item.get("status", "WATCH")))
    log("PRICE: {}".format(format_price(item["price"])))
    log("1H context: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]))
    log("1H: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]))
    log("15M: {} | RSI {:.1f} | VOL {:.2f}x | IMPULSE VOL: {}".format(
        item["m15"]["trend"], item["m15"]["rsi"], item["m15"]["volume_ratio"],
        item.get("volume_impulse_ok", False)
    ))
    if "m5" in item:
        log("5M: {} | RSI {:.1f} | VOL {:.2f}x".format(
            item["m5"]["trend"], item["m5"]["rsi"], item["m5"]["volume_ratio"]
        ))
    log("15M BOS: {} | SWEEP: {} | MICRO BREAK: {}".format(
        item.get("bos15", False), item.get("sweep", False), item.get("micro15", False)
    ))
    log("STRUCTURE SOURCE: {}".format(
        "BOS" if item.get("bos15") else "SWEEP" if item.get("sweep") else
        "MICRO_BREAKOUT" if item.get("micro15") else
        "FIRST_CANDLE_15M" if item.get("first_candle15") == item.get("direction") else "NONE"
    ))
    if "m5" in item:
        log("5M TRIGGER: {} | CONTINUATION: {} | ENTRY PATH: {} | MACD HIST 15m/5m: {:.8f}/{:.8f}".format(
            item.get("trigger5", False), item.get("continuation5", False), item.get("entry_path", "NONE"), item["m15"].get("macd_hist", 0.0),
            item["m5"].get("macd_hist", 0.0)
        ))
    if "structure_ok" in item:
        log("STRUCTURE GATE: {} | 15M SOURCE DIRECTION: {}".format(
            item["structure_ok"], item.get("structure", "NONE")
        ))
    if "pullback_path" in item:
        log("ENTRY PATH CHECK: PULLBACK={} | CONTINUATION={} | FIRST_CANDLE={} | TRIGGER_OK={}".format(
            item.get("pullback_path"), item.get("continuation_path"),
            item.get("first_candle_path", False), item.get("trigger_ok")
        ))
    if item.get("rejection_reason"):
        log("CONFIRMATION BLOCK: {}".format(item["rejection_reason"]))
    if item.get("status") == "CONFIRMED" and "plan" in item:
        plan = item["plan"]
        log("ENTRY: {}".format(format_price(plan["entry"])))
        log("STOP LOSS: {}".format(format_price(plan["sl"])))
        for i, target in enumerate(plan["tp"], 1):
            log("TP{}: {}".format(i, format_price(target)))
        log("STOP DISTANCE: {:.2f} ATR | R/R: {:.2f}".format(plan["risk_atr"], plan["rr"]))
    else:
        log("WATCH ONLY - WAIT FOR CONFIRMATION")
    log("=" * 58)


def main():
    started = time.time()
    log("")
    log("ARAD CRYPTO PRO V6.5.7 CONFIRMED-ONLY QUALITY TEST OURBIT")
    log("START: " + now_text())
    log("MODE: V6.5.7 CONFIRMED-ONLY | strict quality filters | no orders")
    log("BASE URL: " + BASE_URL)
    log("V6.5.7 QUALITY PATCH: 5m first impulse + 15m direction; 1h context only; confirmed-only; no 4h gate")

    ping = api_get("/api/v1/contract/ping")
    if ping is None:
        log("PING: FAILED")
        return
    log("PING: OK")

    contracts = get_contracts()
    if not contracts:
        log("ERROR: CONTRACT DETAIL EMPTY")
        return
    tickers = get_tickers()
    log("CONTRACTS RECEIVED: {}".format(len(contracts)))
    log("TICKERS RECEIVED: {}".format(len(tickers)))

    universe = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        symbol = str(contract.get("symbol", "")).upper()
        if not symbol or not is_target_symbol(symbol) or not contract_is_enabled(contract):
            continue
        ticker = tickers.get(symbol, {})
        price = ticker_price(ticker)
        if price <= 0:
            continue
        universe.append({"symbol": symbol, "ticker": ticker, "turnover": ticker_turnover(ticker)})

    universe.sort(key=lambda x: x["turnover"], reverse=True)
    pinned = [x for x in universe if any(word in x["symbol"] for word in METAL_KEYWORDS)]
    selected = universe[:MAX_STAGE1_SYMBOLS]
    selected_symbols = {x["symbol"] for x in selected}
    for item in pinned:
        if item["symbol"] not in selected_symbols:
            selected.append(item)
            selected_symbols.add(item["symbol"])

    log("ELIGIBLE CONTRACTS: {}".format(len(universe)))
    log("STAGE 1 SCAN COUNT: {}".format(len(selected)))
    log("METAL CONTRACTS PINNED: {}".format(len(pinned)))

    # Predictive EARLY alerts are disabled in confirmed-only mode to save API calls.
    early_alerts = []
    log("EARLY 5M/15M TELEGRAM ALERTS: DISABLED (confirmed-only mode)")



    stage1 = []
    for i, item in enumerate(selected, 1):
        if PRINT_ALL_STAGES:
            log("STAGE 1: {} / {} {}".format(i, len(selected), item["symbol"]))
        result = analyze_symbol(item["symbol"], item["ticker"])
        if result:
            stage1.append(result)
            log("PROMISING: {} {} SCORE {}".format(result["symbol"], result["direction"], result["score"]))

    stage1.sort(key=lambda x: x["score"], reverse=True)
    stage1 = stage1[:MAX_STAGE2_SYMBOLS]
    log("STAGE 1 COMPLETE: {}".format(len(stage1)))

    stage2 = []
    for i, candidate in enumerate(stage1, 1):
        log("STAGE 2: {} / {} {}".format(i, len(stage1), candidate["symbol"]))
        result = analyze_stage2(candidate)
        if result:
            stage2.append(result)
            log("STAGE 2 PASS: {} {} SCORE {}".format(
                result["symbol"], result["direction"], result["score"]
            ))

    stage2.sort(key=candidate_quality, reverse=True)
    stage2 = stage2[:MAX_STAGE3_SYMBOLS]
    log("STAGE 2 COMPLETE: {}".format(len(stage2)))

    # CONFIRMED-ONLY mode: no EARLY or WATCH messages are sent.
    log("TELEGRAM MODE: CONFIRMED ONLY | strict quality gates | outcomes tracked | no orders.")

    final_results = []
    for i, candidate in enumerate(stage2, 1):
        log("STAGE 3: {} / {} {}".format(i, len(stage2), candidate["symbol"]))
        result = analyze_stage3(candidate)
        if result:
            final_results.append(result)
            print_result(result)

    confirmed = [x for x in final_results if x.get("status") == "CONFIRMED"]
    watch = [x for x in final_results if x.get("status") == "WATCH"]

    # Send only final CONFIRMED setups with entry, SL and TP plan.
    log("TELEGRAM ALERT QUEUE: 0 EARLY + 0 WATCH + {} CONFIRMED".format(len(confirmed)))
    dispatch_alerts([], [], confirmed)

    elapsed = time.time() - started
    log("")
    log("=" * 58)
    log("SCAN FINISHED")
    log("TIME: " + now_text())
    log("ELIGIBLE CONTRACTS SCANNED: {}".format(len(selected)))
    log("STAGE 1 PROMISING: {}".format(len(stage1)))
    log("STAGE 2 PASSED / WATCH CANDIDATES: {}".format(len(stage2)))
    log("UNIFIED EARLY 5M/15M SIGNALS FOUND: {}".format(len(early_alerts)))
    log("CONFIRMED SETUPS: {}".format(len(confirmed)))
    log("WATCH SETUPS: {}".format(len(watch)))
    log("ELAPSED: {:.1f} seconds".format(elapsed))
    rejection_counts = {}
    path_counts = {"PULLBACK": 0, "MOMENTUM_CONTINUATION": 0, "BOTH": 0, "NONE": 0}
    gate_counts = {
        "HTF": 0, "HTF_RSI_EXHAUSTION": 0, "15M_STRUCTURE": 0, "ENTRY_PATH": 0, "RSI_MACD_TREND": 0, "15M_DIRECTION_5M_TRIGGER": 0,
        "LATE_ENTRY": 0, "VOLUME": 0, "LIVE_PRICE_GAP": 0, "RISK_OR_NEARBY_STRUCTURE": 0,
    }
    for item in final_results:
        pb = bool(item.get("pullback_path"))
        mc = bool(item.get("continuation_path"))
        if pb and mc:
            path_counts["BOTH"] += 1
        elif pb:
            path_counts["PULLBACK"] += 1
        elif mc:
            path_counts["MOMENTUM_CONTINUATION"] += 1
        else:
            path_counts["NONE"] += 1
        reason = item.get("rejection_reason", "UNKNOWN")
        for part in reason.split(","):
            if part:
                rejection_counts[part] = rejection_counts.get(part, 0) + 1
                if part in gate_counts:
                    gate_counts[part] += 1
    log("ENTRY PATH COUNTS:")
    for name, count in path_counts.items():
        log("  {}: {}".format(name, count))
    log("FAILED GATE COUNTS:")
    for name, count in gate_counts.items():
        log("  {}: {}".format(name, count))
    log("REJECTION COUNTS:")
    for reason, count in sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0])):
        log("  {}: {}".format(reason, count))
    log("NO ORDERS WERE PLACED")
    log("=" * 58)


if __name__ == "__main__":
    main()
