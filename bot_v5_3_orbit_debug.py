import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.3 ORBIT DEBUG
# OURBIT USDT PERPETUALS + CRYPTO + GOLD/SILVER
# MULTI-TIMEFRAME + BOS + PULLBACK + RISK MANAGEMENT
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://contract.ourbit.com"

MAX_COINS = 20
CANDLE_LIMIT = 240
REQUEST_DELAY = 0.15
REQUEST_TIMEOUT = 20

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

# Stocks, ETFs, indices and unrelated leveraged products stay excluded.
# GOLD and SILVER are intentionally NOT excluded.
EXCLUDED_BASES = {
    "SOXL", "SOXS", "TESLA", "TSLA", "KORU",
    "MUSTOCK", "SNDKSTOCK", "USOIL", "UKOIL",
    "SPY", "QQQ", "NVDA", "AAPL", "AMZN", "META",
    "MSFT", "GOOGL", "COIN", "MSTR", "NFLX", "AMD",
    "INTC", "NVD", "TQQQ", "SQQQ", "UVXY"
}

EXCLUDED_WORDS = (
    "STOCK", "EQUITY", "ETF", "OIL", "INDEX",
    "NASDAQ", "NYSE", "COMMODITY"
)

MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF",
    "FLOKI", "BOME", "MEME", "TURBO"
}


# ----------------------------------------------------------
# API HELPERS - OURBIT CONTRACT API
# ----------------------------------------------------------

def api_get(path, params=None):
    url = BASE_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ARAD-CRYPTO-PRO-V5.3-ORBIT",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            result = json.loads(raw)

        if not isinstance(result, dict):
            print("API FORMAT ERROR:", path, "response is not an object")
            return None

        # Ourbit contract responses commonly use success=true and code=0.
        # Accept code=0 as well, while rejecting explicit API errors.
        success = result.get("success")
        code = result.get("code")

        if success is False:
            print("API REJECTED:", path, "code=", code,
                  "msg=", result.get("msg", result.get("message", "")))
            return None

        if code is not None and str(code) not in ("0", "200"):
            print("API ERROR CODE:", path, "code=", code,
                  "msg=", result.get("msg", result.get("message", "")))
            return None

        if "data" not in result:
            print("API DATA MISSING:", path)
            return None

        return result.get("data")

    except Exception as error:
        print("API ERROR:", path, str(error)[:180])
        return None


def get_contracts():
    data = api_get("/api/v1/contract/detail")

    if isinstance(data, dict):
        # Some API responses may wrap the contract array.
        for key in ("contracts", "list", "rows"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        print("CONTRACT FORMAT ERROR: expected a list")
        return []

    accepted = []

    for item in data:
        if not isinstance(item, dict):
            continue

        symbol = str(item.get("symbol", "")).upper()
        base = str(item.get("baseCoin", "")).upper()
        quote = str(item.get("quoteCoin", "")).upper()
        settle = str(item.get("settleCoin", "")).upper()

        if not symbol.endswith("_USDT"):
            continue

        if quote != "USDT" or settle != "USDT":
            continue

        if not base or base in EXCLUDED_BASES:
            continue

        description = (
            base + " " + str(item.get("displayNameEn", ""))
        ).upper()

        if any(word in description for word in EXCLUDED_WORDS):
            continue

        # Ourbit contract state 0 is normally the enabled state.
        state = item.get("state")
        if state is not None and str(state) not in ("0", "ONLINE", "online"):
            continue

        accepted.append(item)

    return accepted


def get_tickers():
    data = api_get("/api/v1/contract/ticker")

    if isinstance(data, dict):
        # A single ticker object or a wrapped list.
        for key in ("list", "rows", "tickers"):
            if isinstance(data.get(key), list):
                return data[key]
        if data.get("symbol"):
            return [data]

    if isinstance(data, list):
        return data

    print("TICKER FORMAT ERROR: expected a list")
    return []


def get_live_price(ticker):
    if not isinstance(ticker, dict):
        return 0.0

    for key in ("lastPrice", "last", "last_price", "close"):
        try:
            value = float(ticker.get(key, 0) or 0)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass

    return 0.0


def get_quote_volume(ticker):
    # Prefer quote-currency turnover/amount, not base-coin volume.
    for key in ("amount24", "turnover24", "quoteVolume", "amount", "turnover"):
        try:
            value = float(ticker.get(key, 0) or 0)
            if value > 0:
                return value
        except (ValueError, TypeError):
            pass
    return 0.0


def parse_candle_data(data):
    """Accept Ourbit/MEXC-style parallel arrays and common row formats."""
    candles = []

    if isinstance(data, dict):
        time_values = data.get("time")
        open_values = data.get("open")
        high_values = data.get("high")
        low_values = data.get("low")
        close_values = data.get("close")
        volume_values = data.get("vol", data.get("volume"))

        if all(isinstance(x, list) for x in (
            time_values, open_values, high_values,
            low_values, close_values, volume_values
        )):
            count = min(
                len(time_values), len(open_values), len(high_values),
                len(low_values), len(close_values), len(volume_values)
            )
            for i in range(count):
                try:
                    timestamp = int(float(time_values[i]))
                    if timestamp > 10**12:
                        timestamp //= 1000
                    candles.append({
                        "time": timestamp,
                        "open": float(open_values[i]),
                        "high": float(high_values[i]),
                        "low": float(low_values[i]),
                        "close": float(close_values[i]),
                        "volume": float(volume_values[i])
                    })
                except (ValueError, TypeError, IndexError):
                    continue
            return candles

        # Sometimes the candle list is nested in a data object.
        for key in ("list", "rows", "candles", "result"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if isinstance(data, list):
        for row in data:
            try:
                if isinstance(row, dict):
                    timestamp = int(float(row.get("time", row.get("timestamp"))))
                    candle = {
                        "time": timestamp,
                        "open": float(row.get("open")),
                        "high": float(row.get("high")),
                        "low": float(row.get("low")),
                        "close": float(row.get("close")),
                        "volume": float(row.get("vol", row.get("volume", 0)))
                    }
                elif isinstance(row, (list, tuple)) and len(row) >= 6:
                    # Expected order: time, open, high, low, close, volume.
                    timestamp = int(float(row[0]))
                    candle = {
                        "time": timestamp,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5])
                    }
                else:
                    continue

                if candle["time"] > 10**12:
                    candle["time"] //= 1000
                candles.append(candle)
            except (ValueError, TypeError, IndexError):
                continue

    return candles


def get_candles(symbol, interval, seconds):
    # Ourbit's contract kline endpoint accepts a start/end time range.
    # Request a little extra history to ensure 205+ closed candles remain.
    end_time = int(time.time())
    start_time = end_time - seconds * (CANDLE_LIMIT + 30)

    data = api_get(
        "/api/v1/contract/kline/" + urllib.parse.quote(symbol, safe="_"),
        {
            "interval": interval,
            "start": start_time,
            "end": end_time
        }
    )

    if data is None:
        return None

    candles = parse_candle_data(data)
    now = int(time.time())
    closed = []

    for candle in candles:
        try:
            if (
                candle["close"] <= 0
                or candle["high"] < candle["low"]
                or candle["volume"] < 0
            ):
                continue

            # Exclude the currently-forming candle.
            if candle["time"] + seconds > now:
                continue

            closed.append(candle)
        except (KeyError, TypeError):
            continue

    # Remove duplicate timestamps and sort oldest -> newest.
    by_time = {c["time"]: c for c in closed}
    closed = sorted(by_time.values(), key=lambda x: x["time"])

    if len(closed) < 205:
        print(
            "CANDLE HISTORY TOO SHORT:",
            symbol, interval, "received=", len(closed),
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
    above = [x for x in highs if x > price]
    below = [x for x in lows if x < price]

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
    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]
    price = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    rsi = calculate_rsi(closes)
    atr = calculate_atr(candles)

    if None in (e9, e21, e50, e200, rsi, atr):
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
            return None

        results[name] = result

        if name == "5m":
            results["5m_candles"] = candles

    live_price = get_live_price(ticker)
    if live_price <= 0:
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
    ema21 = m5["ema21"]
    m15_level = results["15m"]["bos_level"]

    if not atr or not m15_level:
        return False

    tolerance = atr * 0.25

    if direction == "LONG":
        return all((
            last["low"] <= ema21 + tolerance,
            last["close"] > ema21,
            last["close"] > last["open"],
            last["close"] > previous["close"],
            last["close"] > m15_level
        ))

    if direction == "SHORT":
        return all((
            last["high"] >= ema21 - tolerance,
            last["close"] < ema21,
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
        candidates = [x for x in (structural_stop, atr_stop) if 0 < x < live]

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
        candidates = [x for x in (structural_stop, atr_stop) if x > live]

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
            reasons.append(f"LONG_SCORE_BELOW_7 ({long_points}/7)")
        if short_points < 7:
            reasons.append(f"SHORT_SCORE_BELOW_7 ({short_points}/7)")
        if long_points >= 7 and short_points > 2:
            reasons.append("OPPOSITE_SHORT_SCORE_TOO_HIGH")
        if short_points >= 7 and long_points > 2:
            reasons.append("OPPOSITE_LONG_SCORE_TOO_HIGH")

    status = "NOT CONFIRMED"
    plan = None

    if direction:
        expected_trend = "BULLISH" if direction == "LONG" else "BEARISH"
        expected_bos = "BULLISH_BOS" if direction == "LONG" else "BEARISH_BOS"

        if not (h4["trend"] == expected_trend and h1["trend"] == expected_trend):
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

        if len(reasons) == 0:
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
    return f"{value:.10g}" if value is not None else "N/A"


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
        print(f"TP{index}:", fmt(target))

    print("RISK:", fmt(plan["risk"]))
    print("TP4 RISK/REWARD:", round(plan["rr"], 2))
    print("WARNING: THIS IS NOT AN ORDER.")
    print("==========================================\n")


# ----------------------------------------------------------
# MAIN
# ----------------------------------------------------------

def main():
    print("=" * 64)
    print("ARAD CRYPTO PRO V5.3 ORBIT DEBUG")
    print("OURBIT USDT PERPETUALS | CRYPTO + GOLD + SILVER")
    print("MTF + BOS + PULLBACK + RISK MANAGEMENT")
    print("ANALYSIS ONLY - NO ORDERS - NO TELEGRAM")
    print("=" * 64)

    contracts = get_contracts()
    tickers = get_tickers()

    if not contracts or not tickers:
        print("ERROR: MARKET DATA UNAVAILABLE")
        print("Check Ourbit API access and response format.")
        return

    ticker_map = {
        x.get("symbol"): x
        for x in tickers
        if isinstance(x, dict) and x.get("symbol")
    }

    candidates = []

    for contract in contracts:
        symbol = contract.get("symbol")
        ticker = ticker_map.get(symbol)
        if not ticker:
            continue

        amount = get_quote_volume(ticker)
        price = get_live_price(ticker)

        if amount <= 0 or price <= 0:
            continue

        candidates.append((symbol, amount))

    candidates.sort(key=lambda x: x[1], reverse=True)

    selected = candidates[:MAX_COINS]
    selected_names = {x[0] for x in selected}

    # Include meme coins even when they fall outside the top-volume list.
    for symbol, amount in candidates:
        base = symbol.split("_")[0].upper()
        if base in MEME_COINS and symbol not in selected_names:
            selected.append((symbol, amount))
            selected_names.add(symbol)

    # Make sure gold/silver contracts are included when returned by the API,
    # even if their 24h quote volume ranks below the top MAX_COINS.
    for symbol, amount in candidates:
        base = symbol.split("_")[0].upper()
        if base in ("GOLD", "SILVER") and symbol not in selected_names:
            selected.append((symbol, amount))
            selected_names.add(symbol)

    print("Eligible contracts:", len(contracts))
    print("Tickers received:", len(tickers))
    print("Contracts with usable price/volume:", len(candidates))
    print("Selected symbols:", len(selected))
    print("Selected GOLD/SILVER:", [
        symbol for symbol, _ in selected
        if symbol.split("_")[0].upper() in ("GOLD", "SILVER")
    ])

    confirmed_count = 0

    for symbol, _ in selected:
        print("\n" + "-" * 64)
        print("ANALYZING:", symbol)

        try:
            ticker = ticker_map.get(symbol)
            results = analyze_symbol(symbol, ticker)

            if not results:
                print("RESULT: INSUFFICIENT DATA")
                continue

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

            for tf in ("4h", "1h", "15m", "5m"):
                print_levels(tf, results[tf])

            if difference > MAX_LIVE_DIFF:
                print("WARNING: LIVE PRICE MOVED - RECHECK REQUIRED")

            if plan:
                confirmed_count += 1
                print_plan(plan)

        except Exception as error:
            print("SYMBOL ERROR:", str(error)[:180])

        time.sleep(REQUEST_DELAY)

    print("\n" + "=" * 64)
    print("V5.3 ORBIT ANALYSIS COMPLETED")
    print("REVIEW SETUPS:", confirmed_count)
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 64)


if __name__ == "__main__":
    main()
