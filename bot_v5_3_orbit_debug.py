import json
import time
import urllib.request
import urllib.parse
import urllib.error
import socket
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.4 OURBIT
# OURBIT USDT PERPETUALS | CRYPTO + GOLD + SILVER
# 5m / 15m / 1h / 4h | BOS | SWEEP | PULLBACK | RISK FILTERS
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://futures.ourbit.io"
MAX_COINS = 20
CANDLE_LIMIT = 240
REQUEST_DELAY = 0.20
REQUEST_TIMEOUT = 20
API_RETRIES = 3

MIN_VOLUME_RATIO = 0.80
MIN_RR = 1.30
MAX_STOP_ATR = 2.0
MAX_LIVE_DIFF = 0.50
ATR_SL_MULTIPLIER = 1.20

TIMEFRAMES = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
}

EXCLUDED_BASES = {
    "SOXL", "SOXS", "TESLA", "TSLA", "KORU",
    "MUSTOCK", "SNDKSTOCK", "USOIL", "UKOIL",
    "SPY", "QQQ", "NVDA", "AAPL", "AMZN", "META",
    "MSFT", "GOOGL", "COIN", "MSTR", "NFLX", "AMD",
    "INTC", "NVD", "TQQQ", "SQQQ", "UVXY"
}

# GOLD and SILVER are deliberately allowed even if their description
# contains words such as COMMODITY or METAL.
METAL_BASES = {"GOLD", "SILVER"}

EXCLUDED_WORDS = (
    "STOCK", "EQUITY", "ETF", "OIL", "INDEX",
    "NASDAQ", "NYSE"
)

MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF",
    "FLOKI", "BOME", "MEME", "TURBO"
}


# ----------------------------------------------------------
# GENERAL HELPERS
# ----------------------------------------------------------

def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def unwrap_data(result):
    """Return the API data field while tolerating common wrappers."""
    if not isinstance(result, dict):
        return None
    if "data" in result:
        return result.get("data")
    return None


def api_get(path, params=None):
    url = BASE_URL.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ARAD-CRYPTO-PRO-V5.4",
            "Accept": "application/json",
            "Connection": "close"
        },
        method="GET"
    )

    last_error = None
    for attempt in range(1, API_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                raw = response.read().decode("utf-8-sig")
                result = json.loads(raw)

            if not isinstance(result, dict):
                print("API FORMAT ERROR:", path, "expected JSON object")
                return None

            success = result.get("success")
            code = result.get("code")

            if success is False:
                print(
                    "API REJECTED:", path,
                    "code=", code,
                    "message=", result.get("msg", result.get("message", ""))
                )
                return None

            # Ourbit contract endpoints commonly return code=0.
            if code is not None and str(code) not in ("0", "200"):
                print(
                    "API ERROR CODE:", path,
                    "code=", code,
                    "message=", result.get("msg", result.get("message", ""))
                )
                return None

            if "data" not in result:
                print("API DATA MISSING:", path, "keys=", list(result.keys())[:12])
                return None

            return result.get("data")

        except urllib.error.HTTPError as error:
            last_error = error
            print("HTTP ERROR:", path, "status=", error.code)
            # Retry only server/rate-limit errors.
            if error.code not in (429, 500, 502, 503, 504):
                return None

        except (urllib.error.URLError, socket.gaierror, TimeoutError) as error:
            last_error = error
            print(
                "CONNECTION ERROR:", path,
                "attempt", attempt, "/", API_RETRIES,
                str(error)[:160]
            )

        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print("JSON ERROR:", path, str(error)[:160])
            return None

        except Exception as error:
            print("API ERROR:", path, str(error)[:160])
            return None

        if attempt < API_RETRIES:
            time.sleep(attempt * 2)

    print("API REQUEST FAILED AFTER RETRIES:", path, str(last_error)[:160])
    return None


def test_api_connection():
    print("\nTESTING OURBIT API CONNECTION...")
    data = api_get("/api/v1/contract/ping")
    if data is not None:
        print("PING: OK")
        return True

    # Some deployments may not return a data field for ping.
    # Contract detail is the definitive public market-data test.
    print("PING unavailable or unsupported; testing contract detail...")
    data = api_get("/api/v1/contract/detail")
    if data is not None:
        print("CONTRACT API: REACHABLE")
        return True

    print("OURBIT API CONNECTION: FAILED")
    print("This may be DNS/network access from the GitHub Actions runner.")
    return False


# ----------------------------------------------------------
# CONTRACTS AND TICKERS
# ----------------------------------------------------------

def extract_list(data, keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Some APIs wrap the actual response one level deeper.
        nested = data.get("data")
        if isinstance(nested, (dict, list)):
            return extract_list(nested, keys)
    return []


def contract_is_enabled(item):
    state = item.get("state", item.get("status"))
    if state is None or state == "":
        return True

    normalized = str(state).strip().upper()
    return normalized in {
        "0", "ENABLED", "ONLINE", "TRADING", "ACTIVE", "OPEN"
    }


def contract_base(item):
    base = str(item.get("baseCoin", item.get("base_coin", ""))).upper()
    if base:
        return base
    symbol = str(item.get("symbol", "")).upper()
    return symbol.split("_")[0] if "_" in symbol else ""


def is_eligible_contract(item):
    if not isinstance(item, dict):
        return False

    symbol = str(item.get("symbol", "")).upper()
    if not symbol.endswith("_USDT"):
        return False

    base = contract_base(item)
    if not base or base in EXCLUDED_BASES:
        return False

    quote = str(item.get("quoteCoin", item.get("quote_coin", ""))).upper()
    settle = str(item.get("settleCoin", item.get("settle_coin", ""))).upper()

    # If these fields exist, they must be USDT. If absent, the _USDT
    # symbol suffix is used rather than rejecting every contract.
    if quote and quote != "USDT":
        return False
    if settle and settle != "USDT":
        return False

    if not contract_is_enabled(item):
        return False

    description = (
        base + " " +
        str(item.get("displayNameEn", item.get("displayName", "")))
    ).upper()

    if base not in METAL_BASES:
        if any(word in description for word in EXCLUDED_WORDS):
            return False

    return True


def get_contracts():
    data = api_get("/api/v1/contract/detail")
    contracts = extract_list(data, ("contracts", "list", "rows", "items"))

    if not contracts:
        print(
            "CONTRACT FORMAT ERROR: no contract list found.",
            "Returned type:", type(data).__name__
        )
        if isinstance(data, dict):
            print("CONTRACT DATA KEYS:", list(data.keys())[:20])
        return []

    accepted = [item for item in contracts if is_eligible_contract(item)]
    return accepted


def get_tickers():
    data = api_get("/api/v1/contract/ticker")
    tickers = extract_list(data, ("list", "rows", "tickers", "items"))

    if not tickers and isinstance(data, dict) and data.get("symbol"):
        tickers = [data]

    if not tickers:
        print(
            "TICKER FORMAT ERROR: no ticker list found.",
            "Returned type:", type(data).__name__
        )
        if isinstance(data, dict):
            print("TICKER DATA KEYS:", list(data.keys())[:20])
        return []

    return tickers


def get_live_price(ticker):
    if not isinstance(ticker, dict):
        return 0.0

    for key in ("lastPrice", "last", "last_price", "close", "price"):
        value = as_float(ticker.get(key))
        if value > 0:
            return value
    return 0.0


def get_quote_volume(ticker):
    # amount24 is quote turnover on the contract ticker response.
    for key in (
        "amount24", "turnover24", "quoteVolume",
        "turnover", "amount", "volume24"
    ):
        value = as_float(ticker.get(key))
        if value > 0:
            return value
    return 0.0


# ----------------------------------------------------------
# CANDLE DATA
# ----------------------------------------------------------

def normalize_timestamp(value):
    timestamp = int(float(value))
    # Convert milliseconds to seconds for internal candle timestamps.
    if timestamp > 10**12:
        timestamp //= 1000
    return timestamp


def parse_candle_data(data):
    candles = []

    if isinstance(data, dict):
        time_values = data.get("time", data.get("timestamp"))
        open_values = data.get("open")
        high_values = data.get("high")
        low_values = data.get("low")
        close_values = data.get("close")
        volume_values = data.get("vol", data.get("volume"))

        arrays = (
            time_values, open_values, high_values,
            low_values, close_values, volume_values
        )
        if all(isinstance(value, list) for value in arrays):
            count = min(len(value) for value in arrays)
            for i in range(count):
                try:
                    candles.append({
                        "time": normalize_timestamp(time_values[i]),
                        "open": float(open_values[i]),
                        "high": float(high_values[i]),
                        "low": float(low_values[i]),
                        "close": float(close_values[i]),
                        "volume": float(volume_values[i])
                    })
                except (ValueError, TypeError, IndexError):
                    continue
            return candles

        data = extract_list(data, ("list", "rows", "candles", "result", "items"))

    if isinstance(data, list):
        for row in data:
            try:
                if isinstance(row, dict):
                    timestamp_value = row.get("time", row.get("timestamp"))
                    candle = {
                        "time": normalize_timestamp(timestamp_value),
                        "open": float(row.get("open")),
                        "high": float(row.get("high")),
                        "low": float(row.get("low")),
                        "close": float(row.get("close")),
                        "volume": float(row.get("vol", row.get("volume", 0)))
                    }
                elif isinstance(row, (list, tuple)) and len(row) >= 6:
                    # Ourbit contract Kline row: time, open, high, low, close, vol.
                    candle = {
                        "time": normalize_timestamp(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5])
                    }
                else:
                    continue

                candles.append(candle)
            except (ValueError, TypeError, IndexError):
                continue

    return candles


def get_candles(symbol, interval, seconds):
    # IMPORTANT: Ourbit contract kline start/end are milliseconds.
    path = (
    "/api/v1/contract/kline/"
    + urllib.parse.quote(symbol, safe="_")
)

data = api_get(
    path,
    {
        "interval": interval
    }
)
        }
    )

    if data is None:
        return None

    candles = parse_candle_data(data)
    now_seconds = int(time.time())
    closed = []

    for candle in candles:
        try:
            if (
                candle["close"] <= 0
                or candle["high"] < candle["low"]
                or candle["volume"] < 0
            ):
                continue

            # Ignore the currently-forming candle.
            if candle["time"] + seconds > now_seconds:
                continue

            closed.append(candle)
        except (KeyError, TypeError):
            continue

    # Deduplicate timestamps and sort oldest -> newest.
    by_time = {candle["time"]: candle for candle in closed}
    closed = sorted(by_time.values(), key=lambda item: item["time"])

    if len(closed) < 205:
        print(
            "CANDLE HISTORY TOO SHORT:",
            symbol, interval,
            "received=", len(closed),
            "required=205"
        )
        return None

    return closed[-CANDLE_LIMIT:]


# ----------------------------------------------------------
# INDICATORS
# ----------------------------------------------------------

def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)

    return result


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_atr(candles, period=14):
    if len(candles) <= period:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]
        true_ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        ))

    return sum(true_ranges[-period:]) / period


# ----------------------------------------------------------
# MARKET STRUCTURE
# ----------------------------------------------------------

def find_swings(candles, left=3, right=3):
    highs = []
    lows = []

    for i in range(left, len(candles) - right):
        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = (
            all(high > candles[j]["high"] for j in range(i - left, i))
            and
            all(high >= candles[j]["high"] for j in range(i + 1, i + right + 1))
        )
        is_low = (
            all(low < candles[j]["low"] for j in range(i - left, i))
            and
            all(low <= candles[j]["low"] for j in range(i + 1, i + right + 1))
        )

        if is_high:
            highs.append(high)
        if is_low:
            lows.append(low)

    return highs, lows


def nearest_levels(candles, price):
    highs, lows = find_swings(candles[-120:])
    above = [value for value in highs if value > price]
    below = [value for value in lows if value < price]

    resistance = min(above) if above else None
    support = max(below) if below else None
    return support, resistance


def detect_structure(candles):
    last = candles[-1]
    previous = candles[-100:-4]
    highs, lows = find_swings(previous)

    if not highs or not lows:
        return "NONE", "NONE", None

    recent_high = highs[-1]
    recent_low = lows[-1]
    bos = "NONE"
    sweep = "NONE"
    level = None

    if last["close"] > recent_high:
        bos = "BULLISH_BOS"
        level = recent_high
    elif last["close"] < recent_low:
        bos = "BEARISH_BOS"
        level = recent_low

    if last["high"] > recent_high and last["close"] < recent_high:
        sweep = "BEARISH_SWEEP"
    elif last["low"] < recent_low and last["close"] > recent_low:
        sweep = "BULLISH_SWEEP"

    return bos, sweep, level


# ----------------------------------------------------------
# TIMEFRAME ANALYSIS
# ----------------------------------------------------------

def analyze_timeframe(candles):
    closes = [item["close"] for item in candles]
    volumes = [item["volume"] for item in candles]
    price = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    if any(value is None for value in (e9, e21, e50, e200, rsi, atr)):
        return None

    avg_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 0

    if e9 > e21 > e50 > e200 and price > e21:
        trend = "BULLISH"
    elif e9 < e21 < e50 < e200 and price < e21:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    support, resistance = nearest_levels(candles, price)
    bos, sweep, bos_level = detect_structure(candles)

    return {
        "price": price,
        "trend": trend,
        "rsi": rsi,
        "atr": atr,
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "volume_ratio": volume_ratio,
        "support": support,
        "resistance": resistance,
        "bos": bos,
        "sweep": sweep,
        "bos_level": bos_level
    }


def analyze_symbol(symbol, ticker):
    results = {}

    for name, (interval, seconds) in TIMEFRAMES.items():
        candles = get_candles(symbol, interval, seconds)
        time.sleep(REQUEST_DELAY)

        if not candles:
            print(symbol, name, "DATA INCOMPLETE")
            return None

        result = analyze_timeframe(candles)
        if not result:
            print(symbol, name, "INDICATOR DATA INCOMPLETE")
            return None

        results[name] = result
        if name == "5m":
            results["5m_candles"] = candles

    live_price = get_live_price(ticker)
    if live_price <= 0:
        print(symbol, "LIVE PRICE INVALID")
        return None

    results["live_price"] = live_price
    return results


# ----------------------------------------------------------
# PULLBACK CONFIRMATION
# ----------------------------------------------------------

def check_pullback(results, direction):
    candles = results["5m_candles"]
    m5 = results["5m"]
    last = candles[-1]
    previous = candles[-2]
    atr = m5["atr"]
    ema21_value = m5["ema21"]
    m15_level = results["15m"]["bos_level"]

    if not atr or not m15_level:
        return False

    tolerance = atr * 0.25

    if direction == "LONG":
        return all((
            last["low"] <= ema21_value + tolerance,
            last["close"] > ema21_value,
            last["close"] > last["open"],
            last["close"] > previous["close"],
            last["close"] > m15_level
        ))

    if direction == "SHORT":
        return all((
            last["high"] >= ema21_value - tolerance,
            last["close"] < ema21_value,
            last["close"] < last["open"],
            last["close"] < previous["close"],
            last["close"] < m15_level
        ))

    return False


# ----------------------------------------------------------
# SIGNAL AND RISK CALCULATION
# ----------------------------------------------------------

def build_trade_plan(results, direction):
    live = results["live_price"]
    m5 = results["5m"]
    atr = m5["atr"]

    if not atr or atr <= 0:
        return None

    if direction == "LONG":
        support = m5["support"]
        structural_stop = support - atr * 0.15 if support else 0
        atr_stop = live - atr * ATR_SL_MULTIPLIER
        candidates = [
            value for value in (structural_stop, atr_stop)
            if 0 < value < live
        ]
        if not candidates:
            return None

        stop = max(candidates)
        risk = live - stop

        if risk <= 0 or risk > atr * MAX_STOP_ATR:
            return None

        resistance = results["15m"]["resistance"]
        if resistance and resistance > live:
            if (resistance - live) / risk < MIN_RR:
                return None

        targets = [
            live + risk * 1.0,
            live + risk * 1.5,
            live + risk * 2.0,
            live + risk * 3.0
        ]

    else:
        resistance = m5["resistance"]
        structural_stop = resistance + atr * 0.15 if resistance else 0
        atr_stop = live + atr * ATR_SL_MULTIPLIER
        candidates = [
            value for value in (structural_stop, atr_stop)
            if value > live
        ]
        if not candidates:
            return None

        stop = min(candidates)
        risk = stop - live

        if risk <= 0 or risk > atr * MAX_STOP_ATR:
            return None

        support = results["15m"]["support"]
        if support and support < live:
            if (live - support) / risk < MIN_RR:
                return None

        targets = [
            live - risk * 1.0,
            live - risk * 1.5,
            live - risk * 2.0,
            live - risk * 3.0
        ]

    return {
        "direction": direction,
        "entry": live,
        "stop": stop,
        "risk": risk,
        "targets": targets,
        "rr": 3.0
    }


# ----------------------------------------------------------
# DECISION + REJECTION DIAGNOSTICS
# ----------------------------------------------------------

def make_decision(results):
    h4 = results["4h"]
    h1 = results["1h"]
    m15 = results["15m"]
    m5 = results["5m"]

    long_points = 0
    short_points = 0

    if h4["trend"] == "BULLISH":
        long_points += 3
    elif h4["trend"] == "BEARISH":
        short_points += 3

    if h1["trend"] == "BULLISH":
        long_points += 3
    elif h1["trend"] == "BEARISH":
        short_points += 3

    if m15["bos"] == "BULLISH_BOS":
        long_points += 2
    elif m15["bos"] == "BEARISH_BOS":
        short_points += 2

    if m5["bos"] == "BULLISH_BOS":
        long_points += 1
    elif m5["bos"] == "BEARISH_BOS":
        short_points += 1

    if m5["sweep"] == "BEARISH_SWEEP":
        long_points = max(0, long_points - 2)
    if m5["sweep"] == "BULLISH_SWEEP":
        short_points = max(0, short_points - 2)

    bias = "NO TRADE"
    direction = None
    reasons = []

    if long_points >= 7 and short_points <= 2:
        bias = "LONG BIAS"
        direction = "LONG"
    elif short_points >= 7 and long_points <= 2:
        bias = "SHORT BIAS"
        direction = "SHORT"
    else:
        if long_points < 7:
            reasons.append("LONG_SCORE_BELOW_7 ({}/7)".format(long_points))
        if short_points < 7:
            reasons.append("SHORT_SCORE_BELOW_7 ({}/7)".format(short_points))
        if long_points >= 7 and short_points > 2:
            reasons.append("OPPOSITE_SHORT_SCORE_TOO_HIGH")
        if short_points >= 7 and long_points > 2:
            reasons.append("OPPOSITE_LONG_SCORE_TOO_HIGH")

    status = "NOT CONFIRMED"
    plan = None

    if direction:
        expected_trend = "BULLISH" if direction == "LONG" else "BEARISH"
        expected_bos = "BULLISH_BOS" if direction == "LONG" else "BEARISH_BOS"

        if not (
            h4["trend"] == expected_trend
            and h1["trend"] == expected_trend
        ):
            reasons.append("HIGHER_TIMEFRAMES_NOT_ALIGNED")

        if m15["bos"] != expected_bos:
            reasons.append("15M_BOS_NOT_ALIGNED")

        m15_rsi_ok = (
            48 <= m15["rsi"] <= 70
            if direction == "LONG"
            else 30 <= m15["rsi"] <= 52
        )
        if not m15_rsi_ok:
            reasons.append("15M_RSI_OUT_OF_RANGE")

        m5_rsi_ok = (
            45 <= m5["rsi"] <= 68
            if direction == "LONG"
            else 32 <= m5["rsi"] <= 55
        )
        if not m5_rsi_ok:
            reasons.append("5M_RSI_OUT_OF_RANGE")

        if m5["volume_ratio"] < MIN_VOLUME_RATIO:
            reasons.append("VOLUME_TOO_LOW")

        live_diff = (
            abs(results["live_price"] - m15["price"]) / m15["price"] * 100
            if m15["price"] else 999
        )
        if live_diff > MAX_LIVE_DIFF:
            reasons.append("LIVE_PRICE_MOVED_TOO_FAR")

        if not check_pullback(results, direction):
            reasons.append("PULLBACK_NOT_CONFIRMED")

        if not reasons:
            plan = build_trade_plan(results, direction)
            if plan:
                status = direction + " SETUP TO REVIEW"
            else:
                reasons.append("TRADE_PLAN_REJECTED_BY_RISK_FILTERS")

    if not reasons:
        reasons.append("SETUP_PASSED_ALL_FILTERS")

    return bias, status, long_points, short_points, plan, reasons


# ----------------------------------------------------------
# OUTPUT
# ----------------------------------------------------------

def fmt(value):
    return "{:.10g}".format(value) if value is not None else "N/A"


def print_levels(name, data):
    print(
        name,
        "| Trend:", data["trend"],
        "| RSI:", round(data["rsi"], 2),
        "| Vol:", round(data["volume_ratio"], 2),
        "| BOS:", data["bos"],
        "| Sweep:", data["sweep"]
    )
    print(
        "Support:", fmt(data["support"]),
        "| Resistance:", fmt(data["resistance"])
    )


def print_plan(plan):
    if not plan:
        return

    print("\n========== TRADE PLAN TO REVIEW ==========")
    print("DIRECTION:", plan["direction"])
    print("ENTRY:", fmt(plan["entry"]))
    print("STOP LOSS:", fmt(plan["stop"]))

    for index, target in enumerate(plan["targets"], 1):
        print("TP{}:".format(index), fmt(target))

    print("RISK:", fmt(plan["risk"]))
    print("TP4 RISK/REWARD:", round(plan["rr"], 2))
    print("WARNING: THIS IS NOT AN ORDER.")
    print("==========================================\n")


# ----------------------------------------------------------
# MAIN
# ----------------------------------------------------------

def main():
    print("=" * 68)
    print("ARAD CRYPTO PRO V5.4 OURBIT")
    print("OURBIT USDT PERPETUALS | CRYPTO + GOLD + SILVER")
    print("MTF + BOS + PULLBACK + RISK MANAGEMENT")
    print("ANALYSIS ONLY - NO ORDERS - NO TELEGRAM")
    print("=" * 68)

    if not test_api_connection():
        print("\nSTOP: MARKET DATA NOT AVAILABLE. NO SCAN WAS RUN.")
        return

    contracts = get_contracts()
    tickers = get_tickers()

    if not contracts or not tickers:
        print("ERROR: MARKET DATA UNAVAILABLE")
        print("Check API response format and Ourbit public API access.")
        return

    ticker_map = {
        str(item.get("symbol", "")).upper(): item
        for item in tickers
        if isinstance(item, dict) and item.get("symbol")
    }

    candidates = []
    for contract in contracts:
        symbol = str(contract.get("symbol", "")).upper()
        ticker = ticker_map.get(symbol)
        if not ticker:
            continue

        amount = get_quote_volume(ticker)
        price = get_live_price(ticker)
        if amount > 0 and price > 0:
            candidates.append((symbol, amount))

    candidates.sort(key=lambda item: item[1], reverse=True)

    selected = candidates[:MAX_COINS]
    selected_names = {item[0] for item in selected}

    # Include configured meme coins even if outside the top-volume 20.
    for symbol, amount in candidates:
        base = symbol.split("_")[0].upper()
        if base in MEME_COINS and symbol not in selected_names:
            selected.append((symbol, amount))
            selected_names.add(symbol)

    # Always include GOLD/SILVER if Ourbit returns eligible USDT contracts.
    for symbol, amount in candidates:
        base = symbol.split("_")[0].upper()
        if base in METAL_BASES and symbol not in selected_names:
            selected.append((symbol, amount))
            selected_names.add(symbol)

    print("Eligible contracts:", len(contracts))
    print("Tickers received:", len(tickers))
    print("Contracts with usable price/volume:", len(candidates))
    print("Selected symbols:", len(selected))
    print("Selected GOLD/SILVER:", [
        symbol for symbol, _ in selected
        if symbol.split("_")[0].upper() in METAL_BASES
    ])

    if not selected:
        print("ERROR: No matching USDT perpetual symbols with usable ticker data.")
        return

    confirmed_count = 0
    scanned_count = 0

    for symbol, _ in selected:
        print("\n" + "-" * 68)
        print("ANALYZING:", symbol)

        try:
            ticker = ticker_map.get(symbol)
            results = analyze_symbol(symbol, ticker)

            if not results:
                print("RESULT: INSUFFICIENT DATA")
                continue

            scanned_count += 1
            (
                bias,
                status,
                long_score,
                short_score,
                plan,
                reasons
            ) = make_decision(results)

            live = results["live_price"]
            reference = results["15m"]["price"]
            difference = (
                abs(live - reference) / reference * 100
                if reference else 0
            )

            print("LIVE PRICE:", fmt(live))
            print("LAST CLOSED 15M:", fmt(reference))
            print("PRICE DIFFERENCE %:", round(difference, 3))
            print("MARKET BIAS:", bias)
            print("ENTRY STATUS:", status)
            print("LONG POINTS:", long_score)
            print("SHORT POINTS:", short_score)

            print("REJECTION REASONS:")
            for reason in reasons:
                print("-", reason)

            for timeframe in ("4h", "1h", "15m", "5m"):
                print_levels(timeframe, results[timeframe])

            if difference > MAX_LIVE_DIFF:
                print("WARNING: LIVE PRICE MOVED - RECHECK REQUIRED")

            if plan:
                confirmed_count += 1
                print_plan(plan)

        except Exception as error:
            print("SYMBOL ERROR:", symbol, str(error)[:180])

        time.sleep(REQUEST_DELAY)

    print("\n" + "=" * 68)
    print("V5.4 OURBIT ANALYSIS COMPLETED")
    print("SYMBOLS WITH COMPLETE DATA:", scanned_count)
    print("REVIEW SETUPS:", confirmed_count)
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 68)


if __name__ == "__main__":
    main()
