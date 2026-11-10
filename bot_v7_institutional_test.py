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
# ARAD CRYPTO PRO V7.0 INSTITUTIONAL CONFLUENCE OURBIT
# EARLY WATCH + CONFIRMED TELEGRAM ALERTS
# MTF | BOS | SWEEP | PULLBACK | RISK FILTERS
# ANALYSIS ONLY - NO ORDERS
# ==========================================================

BASE_URL = "https://futures.ourbit.io"
TELEGRAM_API = "https://api.telegram.org"

MAX_STAGE1_SYMBOLS = 120  # smaller universe to keep scheduled runs below the interval
MAX_STAGE2_SYMBOLS = 35
MAX_STAGE3_SYMBOLS = 25
MAX_TELEGRAM_WATCH_ALERTS = 3
MAX_EARLY_ALERTS = 5

TIMEFRAMES = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
}

CANDLE_LIMIT = 220
MIN_CANDLES = 80
REQUEST_TIMEOUT = 18
REQUEST_PAUSE = 0.12

MIN_VOLUME_RATIO = 0.75
MIN_SCORE_WATCH = 5
MIN_SCORE_TRADE = 8
MIN_RR = 1.30
MAX_STOP_ATR = 2.00
MAX_LIVE_DIFF = 0.35
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
EXTREME_RSI_SHORT_15M = 25.0
EXTREME_RSI_SHORT_5M = 22.0
EXTREME_RSI_LONG_15M = 72.0
EXTREME_RSI_LONG_5M = 75.0
# Higher-timeframe exhaustion guard for confirmed entries.
EXTREME_RSI_LONG_1H = 75.0
EXTREME_RSI_SHORT_1H = 25.0
CONFIRM_MIN_VOLUME_RATIO = 0.90
TRIGGER_MIN_BODY_RATIO = 0.45
TRIGGER_CLOSE_EDGE = 0.25

# Persistent anti-duplicate settings
WATCH_COOLDOWN_MIN = 45
CONFIRMED_COOLDOWN_MIN = 60
STATE_FILE = "arad_v7_test_signal_state.json"
OUTCOME_FILE = "arad_v7_signal_outcomes.json"
EARLY_COOLDOWN_MIN = 45
CANDLE_CACHE = {}

EXCLUDED_BASES = {
    "USDC", "USDE", "DAI", "FDUSD", "TUSD", "USDP",
    "EUR", "GBP", "AUD", "JPY", "BRL", "TRY", "INDEX", "TEST",
    # Exchange-listed synthetic equities, ETFs and commodity references.
    "MSFT", "GOOGL", "GOOG", "AAPL", "AMZN", "NVDA", "META", "TSLA",
    "AVGO", "AMD", "NFLX", "COIN", "MSTR", "TQQQ", "SQQQ",
    "PAXG", "XAUT", "XPT", "XPD", "XAU", "XAG", "GOLD", "SILVER",
    # Additional synthetic equity / ETF / commodity contracts observed in V7.1.
    "SPY", "QQQ", "SOXL", "SOXS", "SOXSX", "USO", "NATGAS", "NG",
    "INTC", "MU", "MRNA", "SKHYNIX", "SNDK", "COHR", "SPCX", "NBIS",
    "ASTS", "IREN", "CRCL", "SMSN", "BMNR",
}
NON_CRYPTO_CLASS_WORDS = (
    "STOCK", "EQUITY", "ETF", "COMMODITY", "METAL", "FOREX",
    "INDEX", "SHARE", "TOKENIZED STOCK", "PRECIOUS",
)
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
    request_headers = {"User-Agent": "ARAD-CRYPTO-PRO/7.0", "Accept": "application/json"}
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


def is_target_symbol(symbol, contract=None):
    """Conservative crypto-perpetual filter; reject explicit non-crypto metadata."""
    symbol = str(symbol).upper().strip()
    if not symbol.endswith("_USDT"):
        return False
    base = symbol[:-5]
    if base in EXCLUDED_BASES or len(base) < 2:
        return False
    if any(word in base for word in ("GOLD", "SILVER", "XAU", "XAG", "OIL", "BRENT", "WTI")):
        return False
    if isinstance(contract, dict):
        # Field names vary between exchange contract-detail versions.
        for key in (
            "category", "assetClass", "asset_class", "typeName", "displayName",
            "underlyingType", "underlying", "instrumentType", "productType",
            "contractType", "sector", "industry", "baseCoinName", "underlyingName"
        ):
            value = str(contract.get(key, "")).upper()
            if value and any(word in value for word in NON_CRYPTO_CLASS_WORDS):
                return False
        for key in ("isStock", "isCommodity", "isEtf", "isIndex"):
            value = contract.get(key)
            if value is True or str(value).lower() in ("1", "true", "yes"):
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


# ==========================================================
# V7 INSTITUTIONAL / MARKET MICROSTRUCTURE CONTEXT
# Candle-derived proxies are explicitly labeled; no false claim
# of exchange-level order-book/CVD data.
# ==========================================================

def _typical_price(c):
    return (c["high"] + c["low"] + c["close"]) / 3.0


def rolling_vwap(candles, lookback=48):
    rows = candles[-lookback:]
    denom = sum(max(c["volume"], 0.0) for c in rows)
    if denom <= 0:
        return None
    return sum(_typical_price(c) * max(c["volume"], 0.0) for c in rows) / denom


def volume_profile(candles, bins=24, value_area_fraction=0.70, lookback=96):
    rows = candles[-lookback:]
    if len(rows) < 20:
        return None
    lo = min(c["low"] for c in rows)
    hi = max(c["high"] for c in rows)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    histogram = [0.0] * bins
    for c in rows:
        idx = int((_typical_price(c) - lo) / step)
        idx = max(0, min(bins - 1, idx))
        histogram[idx] += max(c["volume"], 0.0)
    total = sum(histogram)
    if total <= 0:
        return None
    poc_idx = max(range(bins), key=lambda i: histogram[i])
    selected = {poc_idx}
    accumulated = histogram[poc_idx]
    left, right = poc_idx - 1, poc_idx + 1
    target = total * value_area_fraction
    while accumulated < target and (left >= 0 or right < bins):
        lv = histogram[left] if left >= 0 else -1
        rv = histogram[right] if right < bins else -1
        if rv > lv:
            selected.add(right)
            accumulated += max(rv, 0)
            right += 1
        else:
            selected.add(left)
            accumulated += max(lv, 0)
            left -= 1
    return {
        "poc": lo + (poc_idx + 0.5) * step,
        "vah": lo + (max(selected) + 1) * step,
        "val": lo + min(selected) * step,
        "coverage": accumulated / total,
    }


def smart_money_context(candles, direction):
    """Closed-candle approximation of structure, FVG, OB, equal highs/lows and value."""
    if len(candles) < 40:
        return {"ready": False, "score": 0, "reasons": ["INSUFFICIENT_CANDLES"]}
    last = candles[-1]
    current_atr = atr(candles)
    if current_atr <= 0:
        return {"ready": False, "score": 0, "reasons": ["ATR_UNAVAILABLE"]}
    recent = candles[-40:-1]
    prior_high = max(c["high"] for c in recent)
    prior_low = min(c["low"] for c in recent)
    prior_trend = "LONG" if candles[-8]["close"] > candles[-20]["close"] else "SHORT"
    bos_up = last["close"] > prior_high
    bos_down = last["close"] < prior_low
    choch_up = bos_up and prior_trend == "SHORT"
    choch_down = bos_down and prior_trend == "LONG"
    sweep_up = last["low"] < min(c["low"] for c in candles[-6:-1]) and last["close"] > min(c["low"] for c in candles[-6:-1])
    sweep_down = last["high"] > max(c["high"] for c in candles[-6:-1]) and last["close"] < max(c["high"] for c in candles[-6:-1])

    # Three-candle fair value gap, only if the latest closed candle created it.
    bull_fvg = last["low"] > candles[-3]["high"]
    bear_fvg = last["high"] < candles[-3]["low"]
    fvg_zone = None
    fvg_aligned = False
    if direction == "LONG" and bull_fvg:
        fvg_zone = [candles[-3]["high"], last["low"]]
        fvg_aligned = fvg_zone[0] - 0.5 * current_atr <= last["close"] <= fvg_zone[1] + 1.5 * current_atr
    elif direction == "SHORT" and bear_fvg:
        fvg_zone = [last["high"], candles[-3]["low"]]
        fvg_aligned = fvg_zone[0] - 1.5 * current_atr <= last["close"] <= fvg_zone[1] + 0.5 * current_atr

    # Approximate order block: last opposite candle before a 3-candle displacement.
    ob_zone = None
    ob_aligned = False
    scan = candles[-12:-3]
    for idx in range(len(scan) - 1, -1, -1):
        c = scan[idx]
        if direction == "LONG" and c["close"] < c["open"]:
            ob_zone = [c["low"], c["high"]]
            break
        if direction == "SHORT" and c["close"] > c["open"]:
            ob_zone = [c["low"], c["high"]]
            break
    if ob_zone:
        distance = max(ob_zone[0] - last["close"], 0, last["close"] - ob_zone[1])
        ob_aligned = distance <= current_atr * 0.60

    # Equal highs/lows: repeated swing tests within a volatility-scaled tolerance.
    tolerance = current_atr * 0.15
    highs = [c["high"] for c in candles[-25:-1]]
    lows = [c["low"] for c in candles[-25:-1]]
    equal_highs = any(abs(highs[i] - highs[j]) <= tolerance for i in range(len(highs)) for j in range(i + 2, len(highs)))
    equal_lows = any(abs(lows[i] - lows[j]) <= tolerance for i in range(len(lows)) for j in range(i + 2, len(lows)))

    vwap = rolling_vwap(candles)
    profile = volume_profile(candles)
    vwap_aligned = bool(vwap and (last["close"] >= vwap if direction == "LONG" else last["close"] <= vwap))
    profile_aligned = bool(profile and (
        last["close"] >= profile["poc"] if direction == "LONG" else last["close"] <= profile["poc"]
    ))

    # Candle delta is a proxy only, not exchange aggressor-side CVD.
    proxy_delta = 0.0
    for c in candles[-20:]:
        span = max(c["high"] - c["low"], 1e-12)
        signed_pressure = (2.0 * (c["close"] - c["low"]) / span) - 1.0
        proxy_delta += signed_pressure * max(c["volume"], 0.0)
    delta_aligned = proxy_delta > 0 if direction == "LONG" else proxy_delta < 0

    structure_aligned = (bos_up or choch_up or sweep_up) if direction == "LONG" else (bos_down or choch_down or sweep_down)
    score = sum(bool(x) for x in (structure_aligned, fvg_aligned, ob_aligned, vwap_aligned, profile_aligned, delta_aligned))
    reasons = []
    if (bos_up if direction == "LONG" else bos_down): reasons.append("BOS")
    if (choch_up if direction == "LONG" else choch_down): reasons.append("CHoCH")
    if (sweep_up if direction == "LONG" else sweep_down): reasons.append("LIQUIDITY_SWEEP")
    if fvg_aligned: reasons.append("FVG_NEAR")
    if ob_aligned: reasons.append("ORDER_BLOCK_NEAR")
    if vwap_aligned: reasons.append("VWAP_SIDE_OK")
    if profile_aligned: reasons.append("POC_SIDE_OK")
    if delta_aligned: reasons.append("CANDLE_DELTA_PROXY_OK")
    if direction == "SHORT" and equal_highs: reasons.append("EQUAL_HIGHS_LIQUIDITY")
    if direction == "LONG" and equal_lows: reasons.append("EQUAL_LOWS_LIQUIDITY")
    return {
        "ready": True, "score": score, "reasons": reasons,
        "bos": bos_up if direction == "LONG" else bos_down,
        "choch": choch_up if direction == "LONG" else choch_down,
        "sweep": sweep_up if direction == "LONG" else sweep_down,
        "fvg": fvg_zone, "fvg_aligned": fvg_aligned,
        "order_block": ob_zone, "order_block_aligned": ob_aligned,
        "equal_highs": equal_highs, "equal_lows": equal_lows,
        "vwap": vwap, "vwap_aligned": vwap_aligned,
        "profile": profile, "profile_aligned": profile_aligned,
        "candle_delta_proxy": proxy_delta, "delta_aligned": delta_aligned,
    }


def optional_orderflow_snapshot(ticker):
    """Read only fields actually supplied by the exchange ticker; missing stays unavailable."""
    ticker = ticker if isinstance(ticker, dict) else {}
    def first(keys):
        for key in keys:
            if ticker.get(key) not in (None, ""):
                return as_float(ticker.get(key), None)
        return None
    return {
        "open_interest": first(("holdVol", "hold_volume", "openInterest", "open_interest")),
        "funding_rate": first(("fundingRate", "funding_rate")),
        "bid_volume": first(("bidVol", "bid_volume")),
        "ask_volume": first(("askVol", "ask_volume")),
    }


def institutional_self_test():
    sample = []
    price = 100.0
    for i in range(80):
        op = price
        cl = price + (0.25 if i % 3 else -0.10)
        sample.append({"time": i * 300, "open": op, "high": max(op, cl) + 0.2,
                       "low": min(op, cl) - 0.2, "close": cl, "volume": 100 + i})
        price = cl
    assert rolling_vwap(sample) is not None
    assert volume_profile(sample) is not None
    assert smart_money_context(sample, "LONG")["ready"] is True
    assert is_target_symbol("BTC_USDT") is True
    assert is_target_symbol("PEPE_USDT") is True
    assert is_target_symbol("MSFT_USDT") is False
    assert is_target_symbol("PAXG_USDT") is False
    assert is_target_symbol("ABC_USDT", {"category": "STOCK"}) is False
    for synthetic in ("SPY_USDT", "QQQ_USDT", "SOXL_USDT", "NATGAS_USDT", "MRNA_USDT", "INTC_USDT"):
        assert is_target_symbol(synthetic) is False
    assert is_target_symbol("SOL_USDT") is True
    log("V7.2 SELF-TEST: PASS (VWAP / VOLUME PROFILE / SMC / SYNTHETIC FILTER)")

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


def analyze_higher_timeframes(c4, c1):
    m4, m1 = market_metrics(c4), market_metrics(c1)
    long_score = 3 * (m4["trend"] == "LONG") + 3 * (m1["trend"] == "LONG")
    short_score = 3 * (m4["trend"] == "SHORT") + 3 * (m1["trend"] == "SHORT")
    long_score += int(m4["rsi"] > 50) + int(m1["rsi"] > 50)
    short_score += int(m4["rsi"] < 50) + int(m1["rsi"] < 50)
    direction = "LONG" if long_score > short_score else "SHORT" if short_score > long_score else "NONE"
    return direction, long_score, short_score, m4, m1


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
    rr = abs(targets[-1] - entry) / risk
    if stop_atr > MAX_STOP_ATR or rr < MIN_RR:
        return None
    return {"entry": entry, "sl": stop, "tp": targets, "risk_atr": stop_atr, "rr": rr}


def analyze_symbol(symbol, ticker):
    price = ticker_price(ticker)
    if price <= 0:
        return None
    c4 = get_candles(symbol, "4h")
    time.sleep(REQUEST_PAUSE)
    c1 = get_candles(symbol, "1h")
    time.sleep(REQUEST_PAUSE)
    if not c4 or not c1:
        return None
    direction, ls, ss, m4, m1 = analyze_higher_timeframes(c4, c1)
    if direction == "NONE":
        return None
    score = max(ls, ss)
    if score < 4:
        return None
    return {
        "symbol": symbol, "direction": direction, "price": price, "ticker": ticker,
        "long_score": ls, "short_score": ss, "score": score, "m4": m4, "m1": m1,
    }


def analyze_stage2(candidate):
    candles = get_candles(candidate["symbol"], "15m")
    time.sleep(REQUEST_PAUSE)
    if not candles:
        return None
    score, metrics, structure, sweep, bos = score_stage2(
        candidate["direction"], candidate["long_score"], candidate["short_score"], candles
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
    if item.get("m4", {}).get("trend") == direction and item.get("m1", {}).get("trend") == direction:
        score += 2
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
    institutional = smart_money_context(candles, direction)
    institutional["orderflow"] = optional_orderflow_snapshot(candidate.get("ticker", {}))
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

    higher_aligned = candidate["m4"]["trend"] == direction and candidate["m1"]["trend"] == direction
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

    # Keep the 15m structure gate strict and directional.
    structure_ok = (
        (
            candidate["bos15"] or candidate["sweep"]
            or candidate.get("micro15", False)
            or first_candle15 == direction
        )
        and candidate.get("structure") in ("NONE", direction)
    )
    late_ok = late_entry_ok(candidate["m15"], m5, direction)
    htf_rsi_ok = higher_timeframe_rsi_ok(candidate["m1"], direction)
    institutional_ok = institutional.get("ready", False) and institutional.get("score", 0) >= 2
    candidate["institutional"] = institutional
    score += min(institutional.get("score", 0), 4)
    candidate["score"] = score

    # Two distinct entry paths; neither bypasses the common quality/risk gates.
    pullback_path = near_pullback and trigger5
    continuation_path = continuation5
    first_candle_path = first_candle15 == direction and (trigger5 or micro5)
    micro_break_path = micro5 and m5["trend"] == direction and momentum_ok
    entry_path = (
        "PULLBACK" if pullback_path
        else "MOMENTUM_CONTINUATION" if continuation_path
        else "MICRO_BREAKOUT" if micro_break_path
        else "FIRST_CANDLE_15M" if first_candle_path
        else "NONE"
    )
    trigger_ok = pullback_path or continuation_path or micro_break_path or first_candle_path
    candidate["micro_break_path"] = micro_break_path
    candidate["first_candle15"] = first_candle15
    candidate["first_candle_path"] = first_candle_path
    candidate["entry_path"] = entry_path
    candidate["pullback_path"] = pullback_path
    candidate["continuation_path"] = continuation_path
    candidate["trigger_ok"] = trigger_ok
    candidate["structure_ok"] = structure_ok

    confirmed = (
        score >= MIN_SCORE_TRADE and higher_aligned and htf_rsi_ok and structure_ok
        and trigger_ok and momentum_ok and late_ok and volume_ok and live_ok and institutional_ok
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
        if not late_ok: failed.append("LATE_ENTRY")
        if not volume_ok: failed.append("VOLUME")
        if not live_ok: failed.append("LIVE_PRICE_GAP")
        if not institutional_ok: failed.append("INSTITUTIONAL_CONFLUENCE")
        candidate["rejection_reason"] = ",".join(failed) or "SCORE"

    candidate["status"] = "WATCH"
    candidate["live_diff"] = live_diff
    return candidate


def detect_second_candle_pump_dump(candles, live_price):
    """
    Confirm a pump/dump reversal only on the SECOND closed 15m candle.
    The first reversal candle must sweep an extreme after an abnormal impulse;
    the second candle must close beyond the first candle's high/low.
    """
    if len(candles) < 35 or live_price <= 0:
        return None
    first, second = candles[-2], candles[-1]
    prior = candles[-14:-2]
    if len(prior) < 10:
        return None
    m = market_metrics(candles)
    if m["atr"] <= 0:
        return None

    impulse_high = max(c["high"] for c in prior)
    impulse_low = min(c["low"] for c in prior)
    impulse_pct = (prior[-1]["close"] - prior[0]["open"]) / max(prior[0]["open"], 1e-12) * 100.0
    impulse_atr_signed = (prior[-1]["close"] - prior[0]["open"]) / m["atr"]
    # Include the first reversal candle in the spike check; previously the
    # volume calculation sampled only candles BEFORE that reversal candle.
    recent_vol = (sum(c["volume"] for c in prior[-2:]) + first["volume"]) / 3.0
    base_vol = sum(c["volume"] for c in candles[-24:-4]) / 20.0
    vol_multiple = recent_vol / base_vol if base_vol > 0 else 0.0
    abnormal_up = impulse_pct >= 3.0 or impulse_atr_signed >= 3.5
    abnormal_down = impulse_pct <= -3.0 or impulse_atr_signed <= -3.5
    abnormal_volume = vol_multiple >= 1.60

    first_range = max(first["high"] - first["low"], 1e-12)
    second_range = max(second["high"] - second["low"], 1e-12)
    second_body = abs(second["close"] - second["open"]) / second_range
    second_close_pos = (second["close"] - second["low"]) / second_range
    live_gap = abs(live_price - second["close"]) / m["atr"]
    if live_gap > 0.60 or second_range / m["atr"] > 2.20:
        return None

    # Pump top: sweep the local high, bearish first candle, then second candle
    # closes below the first candle low with a decisive body.
    top_sweep = first["high"] >= impulse_high and first["close"] < first["open"]
    short_confirm = (
        abnormal_up and abnormal_volume and top_sweep
        and second["close"] < second["open"]
        and second["close"] < first["low"]
        and second_body >= 0.35 and second_close_pos <= 0.35
    )
    # Dump bottom: sweep the local low, bullish first candle, then second candle
    # closes above the first candle high with a decisive body.
    bottom_sweep = first["low"] <= impulse_low and first["close"] > first["open"]
    long_confirm = (
        abnormal_down and abnormal_volume and bottom_sweep
        and second["close"] > second["open"]
        and second["close"] > first["high"]
        and second_body >= 0.35 and second_close_pos >= 0.65
    )
    if not (short_confirm or long_confirm):
        return None

    direction = "SHORT" if short_confirm else "LONG"
    entry = live_price
    buffer = max(m["atr"] * 0.18, entry * 0.0006)
    stop = max(first["high"], second["high"]) + buffer if direction == "SHORT" else min(first["low"], second["low"]) - buffer
    risk = abs(stop - entry)
    if risk <= 0 or risk / m["atr"] > 1.80:
        return None
    targets = [entry - risk, entry - risk * 1.5] if direction == "SHORT" else [entry + risk, entry + risk * 1.5]
    return {
        "symbol": "", "direction": direction, "price": entry, "entry": entry,
        "sl": stop, "tp": targets, "score": 9,
        "m15": m, "body_ratio": second_body,
        "breakout": True, "trigger": "SECOND_CANDLE_CONFIRMATION",
        "pump_dump_pct": abs(impulse_pct), "pump_dump_volume": vol_multiple,
    }


def detect_early_15m(candles, live_price):
    """
    Detect early continuation OR a confirmed 15m swing-rejection reversal.
    Only closed candles are used. This creates an actionable early-entry alert
    with an invalidation stop; it does not place orders.
    """
    if len(candles) < 30 or live_price <= 0:
        return None
    # A pump/dump reversal is promoted only after its second closed candle confirms.
    second_confirmation = detect_second_candle_pump_dump(candles, live_price)
    if second_confirmation:
        return second_confirmation
    m = market_metrics(candles)
    if not m["atr"] or not m["ema9"] or not m["ema21"]:
        return None

    last = candles[-1]
    previous = candles[-2]
    prior = candles[-5:-1]
    candle_range = max(last["high"] - last["low"], 1e-12)
    body = abs(last["close"] - last["open"])
    body_ratio = body / candle_range
    close_position = (last["close"] - last["low"]) / candle_range
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick_ratio = upper_wick / candle_range
    lower_wick_ratio = lower_wick / candle_range
    prev_ema9 = ema([c["close"] for c in candles[:-1]], 9)
    if not prev_ema9:
        return None

    # Reject late entries, abnormal candles and poor live-price tracking.
    live_gap_atr = abs(live_price - last["close"]) / m["atr"]
    ema21_distance_atr = abs(last["close"] - m["ema21"]) / m["atr"]
    candle_size_atr = candle_range / m["atr"]
    if live_gap_atr > 0.60 or ema21_distance_atr > 1.80 or candle_size_atr > 2.20:
        return None
    if m["volume_ratio"] < 0.75:
        return None

    prior_high = max(c["high"] for c in candles[-7:-1])
    prior_low = min(c["low"] for c in candles[-7:-1])
    short_break = last["close"] < min(c["low"] for c in prior)
    short_cross = previous["close"] >= prev_ema9 and last["close"] < m["ema9"]
    long_break = last["close"] > max(c["high"] for c in prior)
    long_cross = previous["close"] <= prev_ema9 and last["close"] > m["ema9"]

    # FIRST directional candle after an opposing sequence.
    # Require a CLOSED candle, meaningful body, close near its extreme,
    # and a micro break or EMA9 reclaim/loss to avoid alerting on every color change.
    previous_three = candles[-4:-1]
    bearish_before = sum(1 for c in previous_three if c["close"] < c["open"])
    bullish_before = sum(1 for c in previous_three if c["close"] > c["open"])
    first_bullish = (
        last["close"] > last["open"]
        and bearish_before >= 2
        and body_ratio >= 0.45
        and close_position >= 0.68
        and (last["close"] > previous["high"] or long_cross)
        and 28.0 <= m["rsi"] <= 68.0
    )
    first_bearish = (
        last["close"] < last["open"]
        and bullish_before >= 2
        and body_ratio >= 0.45
        and close_position <= 0.32
        and (last["close"] < previous["low"] or short_cross)
        and 32.0 <= m["rsi"] <= 72.0
    )

    # Reversal at a local extreme: sweep the recent swing, then close back
    # inside the range with a rejection wick and directional candle body.
    short_reversal = (
        last["high"] > prior_high
        and last["close"] < prior_high
        and last["close"] < last["open"]
        and upper_wick_ratio >= 0.30
        and close_position <= 0.55
        and 48.0 <= m["rsi"] <= 76.0
    )
    long_reversal = (
        last["low"] < prior_low
        and last["close"] > prior_low
        and last["close"] > last["open"]
        and lower_wick_ratio >= 0.30
        and close_position >= 0.45
        and 24.0 <= m["rsi"] <= 52.0
    )

    direction = None
    trigger = None
    if first_bullish:
        direction, trigger = "LONG", "FIRST_BULLISH_15M_CANDLE"
    elif first_bearish:
        direction, trigger = "SHORT", "FIRST_BEARISH_15M_CANDLE"
    elif short_reversal:
        direction, trigger = "SHORT", "15M_SWING_HIGH_REJECTION"
    elif long_reversal:
        direction, trigger = "LONG", "15M_SWING_LOW_RECLAIM"
    elif (
        last["close"] < last["open"]
        and close_position <= 0.40
        and (short_break or short_cross)
        and 25.0 <= m["rsi"] <= 70.0
        and body_ratio >= 0.40
    ):
        direction = "SHORT"
        trigger = "15M_RANGE_BREAK" if short_break else "15M_EMA9_CROSS"
    elif (
        last["close"] > last["open"]
        and close_position >= 0.60
        and (long_break or long_cross)
        and 30.0 <= m["rsi"] <= 75.0
        and body_ratio >= 0.40
    ):
        direction = "LONG"
        trigger = "15M_RANGE_BREAK" if long_break else "15M_EMA9_CROSS"

    if not direction:
        return None

    entry = live_price
    buffer = max(m["atr"] * 0.15, entry * 0.0005)
    stop = last["high"] + buffer if direction == "SHORT" else last["low"] - buffer
    risk = abs(stop - entry)
    if risk <= 0 or risk / m["atr"] > 1.80:
        return None

    # Keep targets realistic for an early scalp setup.
    targets = (
        [entry - risk, entry - risk * 1.5]
        if direction == "SHORT"
        else [entry + risk, entry + risk * 1.5]
    )
    score = 6
    score += 2 if trigger in ("15M_SWING_HIGH_REJECTION", "15M_SWING_LOW_RECLAIM") else 0
    score += 1 if m["volume_ratio"] >= 1.20 else 0
    score += 1 if body_ratio >= 0.60 or upper_wick_ratio >= 0.40 or lower_wick_ratio >= 0.40 else 0

    return {
        "symbol": "", "direction": direction, "price": entry, "entry": entry,
        "sl": stop, "tp": targets, "score": score, "m15": m,
        "body_ratio": body_ratio, "breakout": short_break if direction == "SHORT" else long_break,
        "trigger": trigger,
    }

def early_entry_message(item):
    direction_fa = "فروش SHORT" if item["direction"] == "SHORT" else "خرید LONG"
    lines = [
        "🟠 ARAD CRYPTO PRO | EARLY 15M ENTRY",
        ("تأیید کندل دوم پس از پامپ/دامپ غیرعادی؛ بررسی ورود با حدضرر" if item.get("trigger") == "SECOND_CANDLE_CONFIRMATION"
         else "هشدار کندل اول ۱۵ دقیقه؛ هنوز ورود قطعی نیست و باید کندل دوم تأیید کند"),
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت: {}".format(direction_fa),
        "قیمت/ورود لحظه هشدار: {}".format(format_price(item["entry"])),
        "حد ضرر: {}".format(format_price(item["sl"])),
        "TP1: {}".format(format_price(item["tp"][0])),
        "TP2: {}".format(format_price(item["tp"][1])),
        "RSI 15m: {:.1f} | حجم: {:.2f}x".format(item["m15"]["rsi"], item["m15"]["volume_ratio"]),
        "محرک: {} | قدرت بدنه: {:.0f}%".format(item["trigger"], item["body_ratio"] * 100),
        "امتیاز شروع حرکت: {}".format(item["score"]),
        "همگرایی V7: {}".format(", ".join(item.get("institutional", {}).get("reasons", [])) or "ندارد"),
        "VWAP: {}".format(format_price(item["institutional"]["vwap"]) if item.get("institutional", {}).get("vwap") else "N/A"),
        "POC: {}".format(format_price(item["institutional"]["profile"]["poc"]) if item.get("institutional", {}).get("profile") else "N/A"),
        "",
        ("✅ کندل دوم شکست تأیید را انجام داده؛ قبل از ورود فاصله قیمت تا ورود و حدضرر را بررسی کن."
         if item.get("trigger") == "SECOND_CANDLE_CONFIRMATION"
         else "⚠️ این فقط هشدار کندل اول است؛ تا تأیید کندل دوم وارد نشو."),
        "پامپ/دامپ: {:.2f}% | حجم موج: {:.2f}x".format(item.get("pump_dump_pct", 0.0), item.get("pump_dump_volume", 0.0)),
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

LAST_TELEGRAM_MESSAGE_ID = None

def telegram_send(message):
    global LAST_TELEGRAM_MESSAGE_ID
    LAST_TELEGRAM_MESSAGE_ID = None
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
            message_data = result.get("result", {})
            LAST_TELEGRAM_MESSAGE_ID = message_data.get("message_id") if isinstance(message_data, dict) else None
            log("TELEGRAM SENT | MESSAGE_ID: {}".format(LAST_TELEGRAM_MESSAGE_ID))
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
    second_after_first = (
        alert_type == "EARLY"
        and item.get("trigger") == "SECOND_CANDLE_CONFIRMATION"
        and previous.get("type") == "EARLY"
        and previous.get("trigger") != "SECOND_CANDLE_CONFIRMATION"
    )
    if second_after_first:
        return True
    if alert_type == "EARLY" and previous.get("type") in ("EARLY", "WATCH", "CONFIRMED"):
        if now - int(previous.get("sent_at", 0)) < EARLY_COOLDOWN_MIN * 60:
            return False
    if previous.get("type") == alert_type and now - int(previous.get("sent_at", 0)) < cooldown * 60:
        return False
    return True


def mark_alert_sent(state, item, alert_type):
    key = item["symbol"] + "|" + item["direction"]
    state[key] = {"type": alert_type, "sent_at": int(time.time()), "trigger": item.get("trigger", "")}
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
        "4H: {} | RSI {:.1f}".format(item["m4"]["trend"], item["m4"]["rsi"]),
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
        "نسبت سود به زیان تا TP6: {:.2f}".format(plan["rr"]),
        "همگرایی نهادی V7: {}".format(", ".join(item.get("institutional", {}).get("reasons", [])) or "ندارد"),
        "VWAP: {}".format(format_price(item["institutional"]["vwap"]) if item.get("institutional", {}).get("vwap") else "N/A"),
        "POC / Value Area: {}".format(
            "{} | VAL {} | VAH {}".format(
                format_price(item["institutional"]["profile"]["poc"]),
                format_price(item["institutional"]["profile"]["val"]),
                format_price(item["institutional"]["profile"]["vah"])
            ) if item.get("institutional", {}).get("profile") else "N/A"
        ),
        "Open Interest: {}".format(item["institutional"]["orderflow"].get("open_interest") if item["institutional"]["orderflow"].get("open_interest") is not None else "N/A"),
        "Funding Rate: {}".format(item["institutional"]["orderflow"].get("funding_rate") if item["institutional"]["orderflow"].get("funding_rate") is not None else "N/A"),
        "امتیاز تحلیلی: {} امتیاز".format(item["score"]),
        "مدیریت پیشنهادی: بعد از تثبیت TP1 حدضرر به ورود با احتساب کارمزد منتقل شود؛ بعد از TP2 تریلینگ پشت آخرین سوئینگ 5m/EMA21.",
        "TP3/TP4 فقط با حفظ ساختار و مومنتوم دنبال شود؛ در برگشت ساختار، خروج مقدم است.",
        "",
        "⚠️ این پیام تحلیل است، نه دستور معامله. سفارش خودکار ثبت نشده است.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def dispatch_alerts(early_items, watch_items, confirmed_items):
    state, sha, state_ok = load_signal_state()
    if not state_ok:
        # Fail closed: avoid flooding Telegram if deduplication cannot be checked.
        log("ALERTS PAUSED: cannot verify duplicate-alert state")
        return

    sent_any = False
    for item in early_items:
        if should_send_alert(state, item, "EARLY"):
            if telegram_send(early_entry_message(item)):
                if item.get("trigger") == "SECOND_CANDLE_CONFIRMATION":
                    journal_ok = save_outcome_signal(item, "EARLY")
                    log("SECOND-CANDLE OUTCOME TRACKING: {}".format("ON" if journal_ok else "FAILED"))
                else:
                    log("FIRST-CANDLE ALERT: heads-up only; no trade outcome recorded")
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
        # First-candle heads-up is not an entry and must not pollute trade outcomes.
        if item.get("trigger") != "SECOND_CANDLE_CONFIRMATION":
            log("OUTCOME JOURNAL SKIP: first-candle heads-up is not an entry")
            return False
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
        "delivery_status": "TELEGRAM_API_ACCEPTED" if LAST_TELEGRAM_MESSAGE_ID else "UNVERIFIED_DELIVERY",
        "telegram_message_id": LAST_TELEGRAM_MESSAGE_ID,
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
    log("4H: {} | RSI {:.1f}".format(item["m4"]["trend"], item["m4"]["rsi"]))
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
    if item.get("institutional"):
        inst = item["institutional"]
        log("V7 CONFLUENCE: {} | SCORE {}".format(", ".join(inst.get("reasons", [])), inst.get("score", 0)))
        log("VWAP: {} | POC: {}".format(
            format_price(inst["vwap"]) if inst.get("vwap") else "N/A",
            format_price(inst["profile"]["poc"]) if inst.get("profile") else "N/A"
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
        log("ENTRY PATH CHECK: PULLBACK={} | CONTINUATION={} | MICRO_BREAK={} | FIRST_CANDLE={} | TRIGGER_OK={}".format(
            item.get("pullback_path"), item.get("continuation_path"),
            item.get("micro_break_path", False), item.get("first_candle_path", False), item.get("trigger_ok")
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
    log("ARAD CRYPTO PRO V7.1 INSTITUTIONAL REVERSAL TEST OURBIT")
    log("START: " + now_text())
    log("MODE: V7 TEST ONLY | institutional confluence filters | no orders")
    log("BASE URL: " + BASE_URL)
    log("V7.1 PATCH: crypto classification filter + pump/dump second-candle confirmation + capped alerts + smaller scan universe")

    if os.getenv("ARAD_SELF_TEST", "0") == "1":
        institutional_self_test()
        return

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
        if not symbol or not is_target_symbol(symbol, contract) or not contract_is_enabled(contract):
            continue
        ticker = tickers.get(symbol, {})
        price = ticker_price(ticker)
        if price <= 0:
            continue
        universe.append({"symbol": symbol, "ticker": ticker, "turnover": ticker_turnover(ticker)})

    universe.sort(key=lambda x: x["turnover"], reverse=True)
    selected = universe[:MAX_STAGE1_SYMBOLS]

    log("ELIGIBLE CONTRACTS: {}".format(len(universe)))
    log("STAGE 1 SCAN COUNT: {}".format(len(selected)))
    log("NON-CRYPTO CLASS FILTER: metadata + observed synthetic-underlier denylist active")

    # Independent 15m early-entry scan across the FULL eligible universe, not only Stage 2 candidates.
    early_alerts = []
    for i, item in enumerate(selected, 1):
        if PRINT_ALL_STAGES:
            log("EARLY 15M SCAN: {} / {} {}".format(i, len(selected), item["symbol"]))
        candles15_early = get_candles(item["symbol"], "15m")
        if candles15_early:
            early = detect_early_15m(candles15_early, ticker_price(item["ticker"]))
            if early:
                early["symbol"] = item["symbol"]
                early_context = smart_money_context(candles15_early, early["direction"])
                early["institutional"] = early_context
                if early_context.get("ready") and early_context.get("score", 0) >= 2:
                    early_alerts.append(early)
                    log("V7 EARLY PASS: {} {} SCORE {} | CONFLUENCE {}".format(
                        item["symbol"], early["direction"], early["score"], early_context["score"]
                    ))
                else:
                    log("V7 EARLY BLOCK: {} {} | insufficient institutional confluence".format(
                        item["symbol"], early["direction"]
                    ))
        time.sleep(REQUEST_PAUSE)

    early_alerts.sort(key=lambda x: (x["score"], x["m15"]["volume_ratio"]), reverse=True)
    early_alerts = early_alerts[:MAX_EARLY_ALERTS]
    log("EARLY 15M SIGNALS FOUND (TOP {}): {}".format(MAX_EARLY_ALERTS, len(early_alerts)))

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

    # TEST MODE: send a capped number of WATCH alerts plus every CONFIRMED alert.
    # WATCH is informational only; CONFIRMED includes an entry/SL/TP plan.
    log("TELEGRAM TEST MODE: EARLY + capped WATCH + CONFIRMED; EARLY/CONFIRMED outcomes tracked; no orders.")

    final_results = []
    for i, candidate in enumerate(stage2, 1):
        log("STAGE 3: {} / {} {}".format(i, len(stage2), candidate["symbol"]))
        result = analyze_stage3(candidate)
        if result:
            final_results.append(result)
            print_result(result)

    confirmed = [x for x in final_results if x.get("status") == "CONFIRMED"]
    watch = [x for x in final_results if x.get("status") == "WATCH"]

    # Send best WATCH candidates for visibility and all CONFIRMED setups.
    watch.sort(key=candidate_quality, reverse=True)
    watch_to_send = watch[:MAX_TELEGRAM_WATCH_ALERTS]
    log("TELEGRAM ALERT QUEUE: {} EARLY + {} WATCH + {} CONFIRMED = {} TOTAL BEFORE DEDUP".format(
        len(early_alerts), len(watch_to_send), len(confirmed),
        len(early_alerts) + len(watch_to_send) + len(confirmed)
    ))
    dispatch_alerts(early_alerts, watch_to_send, confirmed)

    elapsed = time.time() - started
    log("")
    log("=" * 58)
    log("SCAN FINISHED")
    log("TIME: " + now_text())
    log("ELIGIBLE CONTRACTS SCANNED: {}".format(len(selected)))
    log("STAGE 1 PROMISING: {}".format(len(stage1)))
    log("STAGE 2 PASSED / WATCH CANDIDATES: {}".format(len(stage2)))
    log("EARLY 15M SIGNALS FOUND: {}".format(len(early_alerts)))
    log("CONFIRMED SETUPS: {}".format(len(confirmed)))
    log("WATCH SETUPS: {}".format(len(watch)))
    log("ELAPSED: {:.1f} seconds".format(elapsed))
    rejection_counts = {}
    path_counts = {"PULLBACK": 0, "MOMENTUM_CONTINUATION": 0, "BOTH": 0, "NONE": 0}
    gate_counts = {
        "HTF": 0, "HTF_RSI_EXHAUSTION": 0, "15M_STRUCTURE": 0, "ENTRY_PATH": 0, "RSI_MACD_TREND": 0,
        "LATE_ENTRY": 0, "VOLUME": 0, "LIVE_PRICE_GAP": 0, "INSTITUTIONAL_CONFLUENCE": 0, "RISK_OR_NEARBY_STRUCTURE": 0,
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
