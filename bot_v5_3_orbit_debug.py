
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import socket
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.4 OURBIT - EARLY DETECTION
# CRYPTO + MEME + GOLD + SILVER
# 5m / 15m / 1h / 4h
# BOS | SWEEP | PULLBACK | WATCH | RISK MANAGEMENT
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://futures.ourbit.io"

CANDLE_LIMIT = 240
MIN_CANDLES = 205
REQUEST_DELAY = 0.20
REQUEST_TIMEOUT = 20
API_RETRIES = 3

MIN_VOLUME_RATIO = 0.80
MIN_RR = 1.30
MAX_STOP_ATR = 2.0
MAX_LIVE_DIFF = 0.50
ATR_SL_MULTIPLIER = 1.20

WATCH_MIN_SCORE = 5
TRADE_MIN_SCORE = 7

# Maximum number of candidates in the first stage.
# None means all eligible contracts.
MAX_STAGE1_SYMBOLS = None

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
    "INTC", "NVD", "TQQQ", "SQQQ", "UVXY",
    "SPCX", "SKHY", "SKHYNIX", "TSM", "BABA", "PLTR"
}

METAL_BASES = {"GOLD", "XAU", "SILVER"}

EXCLUDED_WORDS = (
    "STOCK", "EQUITY", "ETF", "OIL", "INDEX",
    "NASDAQ", "NYSE"
)

MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF",
    "FLOKI", "BOME", "MEME", "TURBO"
}


# ----------------------------------------------------------
# API
# ----------------------------------------------------------

def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_list(data, keys):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value

        nested = data.get("data")
        if isinstance(nested, (dict, list)):
            return extract_list(nested, keys)

    return []


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
                print("API FORMAT ERROR:", path)
                return None

            if result.get("success") is False:
                print("API REJECTED:", path, result.get("msg"))
                return None

            code = result.get("code")

            if code is not None and str(code) not in ("0", "200"):
                print("API ERROR CODE:", path, code)
                return None

            if "data" not in result:
                print("API DATA MISSING:", path)
                return None

            return result.get("data")

        except urllib.error.HTTPError as error:
            last_error = error
            print("HTTP ERROR:", path, error.code)

            if error.code not in (429, 500, 502, 503, 504):
                return None

        except (
            urllib.error.URLError,
            socket.gaierror,
            TimeoutError
        ) as error:
            last_error = error
            print(
                "CONNECTION ERROR:",
                attempt, "/", API_RETRIES,
                str(error)[:150]
            )

        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print("JSON ERROR:", str(error)[:150])
            return None

        except Exception as error:
            print("API ERROR:", str(error)[:150])
            return None

        if attempt < API_RETRIES:
            time.sleep(attempt * 2)

    print("API FAILED:", path, str(last_error)[:150])
    return None


def test_api_connection():
    print("\nTESTING OURBIT API...")

    data = api_get("/api/v1/contract/ping")

    if data is not None:
        print("PING: OK")
        return True

    data = api_get("/api/v1/contract/detail")

    if data is not None:
        print("CONTRACT API: OK")
        return True

    print("OURBIT API CONNECTION FAILED")
    return False


# ----------------------------------------------------------
# CONTRACTS AND TICKERS
# ----------------------------------------------------------

def contract_base(item):
    base = str(
        item.get("baseCoin", item.get("base_coin", ""))
    ).upper()

    if base:
        return base

    symbol = str(item.get("symbol", "")).upper()
    return symbol.split("_")[0] if "_" in symbol else ""


def contract_is_enabled(item):
    state = item.get("state", item.get("status"))

    if state is None or state == "":
        return True

    return str(state).strip().upper() in {
        "0", "ENABLED", "ONLINE", "TRADING", "ACTIVE", "OPEN"
    }


def is_eligible_contract(item):
    if not isinstance(item, dict):
        return False

    symbol = str(item.get("symbol", "")).upper()

    if not symbol.endswith("_USDT"):
        return False

    base = contract_base(item)

    if not base or base in EXCLUDED_BASES:
        return False

    quote = str(
        item.get("quoteCoin", item.get("quote_coin", ""))
    ).upper()

    settle = str(
        item.get("settleCoin", item.get("settle_coin", ""))
    ).upper()

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

    contracts = extract_list(
        data, ("contracts", "list", "rows", "items")
    )

    accepted = [
        item for item in contracts
        if is_eligible_contract(item)
    ]

    return accepted


def get_tickers():
    data = api_get("/api/v1/contract/ticker")

    tickers = extract_list(
        data, ("list", "rows", "tickers", "items")
    )

    if not tickers and isinstance(data, dict):
        if data.get("symbol"):
            tickers = [data]

    return tickers


def get_live_price(ticker):
    if not isinstance(ticker, dict):
        return 0.0

    for key in (
        "lastPrice", "last", "last_price", "close", "price"
    ):
        value = as_float(ticker.get(key))

        if value > 0:
            return value

    return 0.0


def get_quote_volume(ticker):
    # Prefer quote turnover. Raw volume is not a reliable
    # substitute for USDT turnover.
    for key in (
        "amount24", "turnover24", "quoteVolume",
        "turnover", "amount"
    ):
        value = as_float(ticker.get(key))

        if value > 0:
            return value

    return 0.0


# ----------------------------------------------------------
# CANDLES
# ----------------------------------------------------------

def normalize_timestamp(value):
    timestamp = int(float(value))

    if timestamp > 10**12:
        timestamp //= 1000

    return timestamp


def parse_candle_data(data):
    candles = []

    if isinstance(data, dict):
        times = data.get("time", data.get("timestamp"))
        opens = data.get("open")
        highs = data.get("high")
        lows = data.get("low")
        closes = data.get("close")
        volumes = data.get("vol", data.get("volume"))

        arrays = (times, opens, highs, lows, closes, volumes)

        if all(isinstance(x, list) for x in arrays):
            count = min(len(x) for x in arrays)

            for i in range(count):
                try:
                    candles.append({
                        "time": normalize_timestamp(times[i]),
                        "open": float(opens[i]),
                        "high": float(highs[i]),
                        "low": float(lows[i]),
                        "close": float(closes[i]),
                        "volume": float(volumes[i])
                    })
                except (ValueError, TypeError, IndexError):
                    continue

            return candles

        data = extract_list(
            data, ("list", "rows", "candles", "result", "items")
        )

    if isinstance(data, list):
        for row in data:
            try:
                if isinstance(row, dict):
                    candle = {
                        "time": normalize_timestamp(
                            row.get("time", row.get("timestamp"))
                        ),
                        "open": float(row.get("open")),
                        "high": float(row.get("high")),
                        "low": float(row.get("low")),
                        "close": float(row.get("close")),
                        "volume": float(
                            row.get("vol", row.get("volume", 0))
                        )
                    }

                elif isinstance(row, (list, tuple)) and len(row) >= 6:
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
    path = (
        "/api/v1/contract/kline/"
        + urllib.parse.quote(symbol, safe="_")
    )

    data = api_get(path, {"interval": interval})

    if data is None:
        print("CANDLE DATA MISSING:", symbol, interval)
        return None

    raw_candles = parse_candle_data(data)

    now = int(time.time())
    closed = []

    for candle in raw_candles:
        try:
            if (
                candle["close"] <= 0
                or candle["high"] < candle["low"]
                or candle["volume"] < 0
            ):
                continue

            if candle["time"] + seconds > now:
                continue

            closed.append(candle)

        except (KeyError, TypeError):
            continue

    # Remove duplicate timestamps.
    unique = {
        item["time"]: item for item in closed
    }

    closed = sorted(
        unique.values(),
        key=lambda item: item["time"]
    )

    if len(closed) < MIN_CANDLES:
        print(
            "HISTORY TOO SHORT:",
            symbol, interval, len(closed)
        )
        return None

    # Reject stale data.
    last_closed_time = closed[-1]["time"] + seconds
    age = now - last_closed_time

    if age < 0 or age > seconds * 2.5:
        print(
            "STALE CANDLES:",
            symbol, interval,
            "age_seconds=", age
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

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_atr(candles, period=14):
    if len(candles) <= period:
        return None

    ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        ))

    return sum(ranges[-period:]) / period


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
            all(
                high > candles[j]["high"]
                for j in range(i - left, i)
            )
            and
            all(
                high >= candles[j]["high"]
                for j in range(i + 1, i + right + 1)
            )
        )

        is_low = (
            all(
                low < candles[j]["low"]
                for j in range(i - left, i)
            )
            and
            all(
                low <= candles[j]["low"]
                for j in range(i + 1, i + right + 1)
            )
        )

        if is_high:
            highs.append(high)

        if is_low:
            lows.append(low)

    return highs, lows


def nearest_levels(candles, price):
    highs, lows = find_swings(candles[-120:])

    above = [x for x in highs if x > price]
    below = [x for x in lows if x < price]

    support = max(below) if below else None
    resistance = min(above) if above else None

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

    if (
        last["high"] > recent_high
        and last["close"] < recent_high
    ):
        sweep = "BEARISH_SWEEP"

    elif (
        last["low"] < recent_low
        and last["close"] > recent_low
    ):
        sweep = "BULLISH_SWEEP"

    return bos, sweep, level


# ----------------------------------------------------------
# TIMEFRAME ANALYSIS
# ----------------------------------------------------------

def analyze_timeframe(candles):
    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    price = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    if any(
        x is None for x in (e9, e21, e50, e200, rsi, atr)
    ):
        return None

    avg_volume = sum(volumes[-21:-1]) / 20

    volume_ratio = (
        volumes[-1] / avg_volume
        if avg_volume > 0 else 0
    )

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
        "bos_level": bos_level,
        "last_candle_time": candles[-1]["time"]
    }


def fetch_timeframe(symbol, name):
    interval, seconds = TIMEFRAMES[name]

    candles = get_candles(symbol, interval, seconds)
    time.sleep(REQUEST_DELAY)

    if not candles:
        return None, None

    result = analyze_timeframe(candles)

    if not result:
        return None, None

    return result, candles


# ----------------------------------------------------------
# HIGHER TIMEFRAME SCREENING
# ----------------------------------------------------------

def higher_timeframe_score(h4, h1):
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

    return long_points, short_points


def is_stage1_candidate(h4, h1):
    long_points, short_points = higher_timeframe_score(h4, h1)

    return (
        (long_points >= 3 and short_points <= 3)
        or
        (short_points >= 3 and long_points <= 3)
    )


# ----------------------------------------------------------
# PULLBACK
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
# RISK MANAGEMENT
# ----------------------------------------------------------

def build_trade_plan(results, direction):
    live = results["live_price"]
    m5 = results["5m"]
    atr = m5["atr"]

    if not atr or atr <= 0:
        return None

    if direction == "LONG":
        support = m5["support"]

        structural_stop = (
            support - atr * 0.15 if support else 0
        )

        atr_stop = live - atr * ATR_SL_MULTIPLIER

        candidates = [
            x for x in (structural_stop, atr_stop)
            if 0 < x < live
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
            live + risk,
            live + risk * 1.5,
            live + risk * 2,
            live + risk * 3
        ]

    else:
        resistance = m5["resistance"]

        structural_stop = (
            resistance + atr * 0.15 if resistance else 0
        )

        atr_stop = live + atr * ATR_SL_MULTIPLIER

        candidates = [
            x for x in (structural_stop, atr_stop)
            if x > live
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
            live - risk,
            live - risk * 1.5,
            live - risk * 2,
            live - risk * 3
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
# EARLY WATCH + CONFIRMED DECISION
# ----------------------------------------------------------

def make_decision(results):
    h4 = results["4h"]
    h1 = results["1h"]
    m15 = results["15m"]
    m5 = results["5m"]

    long_points, short_points = higher_timeframe_score(h4, h1)

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

    direction = None
    bias = "NO TRADE"
    status = "NOT CONFIRMED"
    reasons = []
    plan = None

    if long_points >= TRADE_MIN_SCORE and short_points <= 2:
        direction = "LONG"
        bias = "LONG BIAS"

    elif short_points >= TRADE_MIN_SCORE and long_points <= 2:
        direction = "SHORT"
        bias = "SHORT BIAS"

    else:
        # Early warning: do not treat WATCH as an entry.
        if (
            long_points >= WATCH_MIN_SCORE
            and short_points <= 2
        ):
            direction = "LONG"
            bias = "LONG WATCH BIAS"
            status = "LONG WATCH - WAIT FOR CONFIRMATION"

        elif (
            short_points >= WATCH_MIN_SCORE
            and long_points <= 2
        ):
            direction = "SHORT"
            bias = "SHORT WATCH BIAS"
            status = "SHORT WATCH - WAIT FOR CONFIRMATION"

    if direction and status.startswith("NOT"):
        expected_trend = (
            "BULLISH" if direction == "LONG" else "BEARISH"
        )

        expected_bos = (
            "BULLISH_BOS" if direction == "LONG"
            else "BEARISH_BOS"
        )

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
            abs(results["live_price"] - m15["price"])
            / m15["price"] * 100
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
                reasons.append("RISK_FILTER_REJECTED")

    if status.startswith("LONG WATCH"):
        reasons.append("EARLY_LONG_WARNING_NOT_AN_ENTRY")

    elif status.startswith("SHORT WATCH"):
        reasons.append("EARLY_SHORT_WARNING_NOT_AN_ENTRY")

    elif not reasons:
        reasons.append("SETUP_PASSED_ALL_FILTERS")

    return (
        bias, status,
        long_points, short_points,
        plan, reasons
    )


# ----------------------------------------------------------
# OUTPUT
# ----------------------------------------------------------

def fmt(value):
    return (
        "{:.10g}".format(value)
        if value is not None else "N/A"
    )


def print_levels(name, data):
    candle_time = datetime.fromtimestamp(
        data["last_candle_time"],
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M")

    print(
        name,
        "| Trend:", data["trend"],
        "| RSI:", round(data["rsi"], 2),
        "| Vol:", round(data["volume_ratio"], 2),
        "| BOS:", data["bos"],
        "| Sweep:", data["sweep"],
        "| Candle UTC:", candle_time
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

    for i, target in enumerate(plan["targets"], 1):
        print("TP{}:".format(i), fmt(target))

    print("RISK:", fmt(plan["risk"]))
    print("TP4 R/R:", plan["rr"])
    print("WARNING: NOT AN ORDER")
    print("==========================================\n")


# ----------------------------------------------------------
# MAIN - TWO STAGE SCANNER
# ----------------------------------------------------------

def main():
    print("=" * 68)
    print("ARAD CRYPTO PRO V5.4 OURBIT - EARLY DETECTION")
    print("CRYPTO + MEME + GOLD + SILVER")
    print("WATCH + BOS + PULLBACK + RISK FILTERS")
    print("ANALYSIS ONLY - NO ORDERS - NO TELEGRAM")
    print("=" * 68)

    if not test_api_connection():
        print("STOP: API CONNECTION FAILED")
        return

    contracts = get_contracts()
    tickers = get_tickers()

    if not contracts or not tickers:
        print("ERROR: CONTRACT OR TICKER DATA MISSING")
        return

    ticker_map = {
        str(x.get("symbol", "")).upper(): x
        for x in tickers
        if isinstance(x, dict) and x.get("symbol")
    }

    candidates = []

    for contract in contracts:
        symbol = str(contract.get("symbol", "")).upper()
        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        price = get_live_price(ticker)
        amount = get_quote_volume(ticker)

        if price > 0 and amount > 0:
            candidates.append((symbol, amount))

    candidates.sort(
        key=lambda x: x[1],
        reverse=True
    )

    if MAX_STAGE1_SYMBOLS is not None:
        stage1_symbols = candidates[:MAX_STAGE1_SYMBOLS]
    else:
        stage1_symbols = candidates

    print("Eligible contracts:", len(contracts))
    print("Tickers received:", len(tickers))
    print("Usable candidates:", len(candidates))
    print("Stage 1 symbols:", len(stage1_symbols))
    print("Stage 1 mode:", "ALL" if MAX_STAGE1_SYMBOLS is None else "LIMITED")

    # Stage 1: only 4h and 1h.
    promising = []

    for index, (symbol, amount) in enumerate(stage1_symbols, 1):
        print(
            "\nSTAGE 1:",
            index, "/", len(stage1_symbols),
            symbol
        )

        try:
            h4, _ = fetch_timeframe(symbol, "4h")

            if not h4:
                continue

            h1, _ = fetch_timeframe(symbol, "1h")

            if not h1:
                continue

            if is_stage1_candidate(h4, h1):
                lp, sp = higher_timeframe_score(h4, h1)

                promising.append({
                    "symbol": symbol,
                    "amount": amount,
                    "4h": h4,
                    "1h": h1,
                    "long_points": lp,
                    "short_points": sp
                })

                print(
                    "PROMISING:",
                    symbol,
                    "LONG:", lp,
                    "SHORT:", sp
                )

        except Exception as error:
            print("STAGE 1 ERROR:", symbol, str(error)[:150])

        time.sleep(REQUEST_DELAY)

    print("\nSTAGE 1 COMPLETED")
    print("Promising symbols:", len(promising))

    # Stage 2: 15m screening.
    stage2 = []

    for item in promising:
        symbol = item["symbol"]

        print("\nSTAGE 2 - 15M:", symbol)

        try:
            m15, _ = fetch_timeframe(symbol, "15m")

            if not m15:
                continue

            lp = item["long_points"]
            sp = item["short_points"]

            if m15["bos"] == "BULLISH_BOS":
                lp += 2

            elif m15["bos"] == "BEARISH_BOS":
                sp += 2

            if (
                lp >= WATCH_MIN_SCORE and sp <= 2
            ) or (
                sp >= WATCH_MIN_SCORE and lp <= 2
            ):
                item["15m"] = m15
                item["stage2_long"] = lp
                item["stage2_short"] = sp
                stage2.append(item)

                print(
                    "STAGE 2 PASS:",
                    symbol,
                    "LONG:", lp,
                    "SHORT:", sp,
                    "15M BOS:", m15["bos"]
                )

            else:
                print(
                    "STAGE 2 REJECT:",
                    symbol,
                    "LONG:", lp,
                    "SHORT:", sp
                )

        except Exception as error:
            print("STAGE 2 ERROR:", symbol, str(error)[:150])

        time.sleep(REQUEST_DELAY)

    print("\nSTAGE 2 COMPLETED")
    print("Early candidates:", len(stage2))

    # Stage 3: 5m confirmation for the shortlisted symbols.
    confirmed_count = 0
    watch_count = 0
    scanned_count = 0

    for item in stage2:
        symbol = item["symbol"]

        print("\n" + "-" * 68)
        print("STAGE 3:", symbol)

        try:
            m5, candles5 = fetch_timeframe(symbol, "5m")

            if not m5:
                print("5M DATA INCOMPLETE")
                continue

            results = {
                "4h": item["4h"],
                "1h": item["1h"],
                "15m": item["15m"],
                "5m": m5,
                "5m_candles": candles5
            }

            ticker = ticker_map.get(symbol)
            live_price = get_live_price(ticker)

            if live_price <= 0:
                print("INVALID LIVE PRICE")
                continue

            results["live_price"] = live_price

            scanned_count += 1

            (
                bias,
                status,
                long_score,
                short_score,
                plan,
                reasons
            ) = make_decision(results)

            reference = results["15m"]["price"]

            difference = (
                abs(live_price - reference)
                / reference * 100
                if reference else 0
            )

            print("LIVE PRICE:", fmt(live_price))
            print("LAST CLOSED 15M:", fmt(reference))
            print("PRICE DIFFERENCE %:", round(difference, 3))

            print("MARKET BIAS:", bias)
            print("ENTRY STATUS:", status)
            print("LONG POINTS:", long_score)
            print("SHORT POINTS:", short_score)

            print("REASONS:")
            for reason in reasons:
                print("-", reason)

            for tf in ("4h", "1h", "15m", "5m"):
                print_levels(tf, results[tf])

            if status.startswith("LONG WATCH") or status.startswith("SHORT WATCH"):
                watch_count += 1

            if plan:
                confirmed_count += 1
                print_plan(plan)

        except Exception as error:
            print("STAGE 3 ERROR:", symbol, str(error)[:180])

        time.sleep(REQUEST_DELAY)

    print("\n" + "=" * 68)
    print("ARAD CRYPTO PRO V5.4 COMPLETED")
    print("ELIGIBLE CONTRACTS:", len(contracts))
    print("STAGE 1 CHECKED:", len(stage1_symbols))
    print("STAGE 1 PROMISING:", len(promising))
    print("STAGE 2 EARLY CANDIDATES:", len(stage2))
    print("STAGE 3 COMPLETE:", scanned_count)
    print("WATCH ALERTS:", watch_count)
    print("REVIEW SETUPS:", confirmed_count)
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 68)


if __name__ == "__main__":
    main()
