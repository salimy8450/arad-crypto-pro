
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.3 DEBUG
# MULTI-TIMEFRAME + BOS + PULLBACK + RISK MANAGEMENT
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://contract.mexc.com"

MAX_COINS = 20
CANDLE_LIMIT = 240
REQUEST_DELAY = 0.12

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
    "GOLD", "SILVER", "SPY", "QQQ", "NVDA",
    "AAPL", "AMZN", "META", "MSFT", "GOOGL",
    "COIN", "MSTR", "NFLX", "AMD", "INTC",
    "NVD", "TQQQ", "SQQQ", "UVXY"
}

EXCLUDED_WORDS = (
    "STOCK", "EQUITY", "ETF", "OIL", "GOLD",
    "SILVER", "INDEX", "NASDAQ", "NYSE", "COMMODITY"
)

MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF",
    "FLOKI", "BOME", "MEME", "TURBO"
}


# ----------------------------------------------------------
# API
# ----------------------------------------------------------

def api_get(path, params=None):
    url = BASE_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ARAD-CRYPTO-PRO-V5.3"}
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not isinstance(result, dict):
            return None

        if not result.get("success", False):
            return None

        return result.get("data")

    except Exception as error:
        print("API ERROR:", str(error)[:120])
        return None


def get_contracts():
    data = api_get("/api/v1/contract/detail")

    if not isinstance(data, list):
        return []

    accepted = []

    for item in data:
        symbol = str(item.get("symbol", ""))
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

        state = item.get("state")

        if state is not None and state != 0:
            continue

        accepted.append(item)

    return accepted


def get_tickers():
    data = api_get("/api/v1/contract/ticker")

    if isinstance(data, dict):
        return [data]

    if isinstance(data, list):
        return data

    return []


def get_live_price(ticker):
    try:
        return float(ticker.get("lastPrice", 0))
    except (ValueError, TypeError, AttributeError):
        return 0.0


def get_candles(symbol, interval, seconds):
    data = api_get(
        "/api/v1/contract/kline/" + symbol,
        {"interval": interval}
    )

    if not isinstance(data, dict):
        return None

    keys = ("time", "open", "high", "low", "close", "vol")

    if not all(key in data for key in keys):
        return None

    count = min(len(data[key]) for key in keys)
    now = int(time.time())
    candles = []

    for i in range(count):
        try:
            timestamp = int(data["time"][i])

            # Exclude currently forming candle.
            if timestamp + seconds > now:
                continue

            candle = {
                "time": timestamp,
                "open": float(data["open"][i]),
                "high": float(data["high"][i]),
                "low": float(data["low"][i]),
                "close": float(data["close"][i]),
                "volume": float(data["vol"][i])
            }

            if (
                candle["close"] <= 0
                or candle["high"] < candle["low"]
                or candle["volume"] < 0
            ):
                continue

            candles.append(candle)

        except (ValueError, TypeError, IndexError):
            continue

    candles.sort(key=lambda x: x["time"])

    if len(candles) < 205:
        return None

    return candles[-CANDLE_LIMIT:]


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

    volume_ratio = (
        volumes[-1] / avg_volume if avg_volume > 0 else 0
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
        touched_ema = last["low"] <= ema21 + tolerance
        recovered_ema = last["close"] > ema21
        bullish_candle = last["close"] > last["open"]
        higher_close = last["close"] > previous["close"]
        held_breakout = last["close"] > m15_level

        return all((
            touched_ema,
            recovered_ema,
            bullish_candle,
            higher_close,
            held_breakout
        ))

    if direction == "SHORT":
        touched_ema = last["high"] >= ema21 - tolerance
        rejected_ema = last["close"] < ema21
        bearish_candle = last["close"] < last["open"]
        lower_close = last["close"] < previous["close"]
        held_breakdown = last["close"] < m15_level

        return all((
            touched_ema,
            rejected_ema,
            bearish_candle,
            lower_close,
            held_breakdown
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
            live + risk * 1.0,
            live + risk * 1.5,
            live + risk * 2.0,
            live + risk * 3.0
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
            reasons.append(
                f"LONG_SCORE_BELOW_7 ({long_points}/7)"
            )

        if short_points < 7:
            reasons.append(
                f"SHORT_SCORE_BELOW_7 ({short_points}/7)"
            )

        if long_points >= 7 and short_points > 2:
            reasons.append("OPPOSITE_SHORT_SCORE_TOO_HIGH")

        if short_points >= 7 and long_points > 2:
            reasons.append("OPPOSITE_LONG_SCORE_TOO_HIGH")

    status = "NOT CONFIRMED"
    plan = None

    if direction:
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

        pullback_ok = check_pullback(results, direction)

        if not pullback_ok:
            reasons.append("PULLBACK_NOT_CONFIRMED")

        filters_ok = len(reasons) == 0

        if filters_ok:
            plan = build_trade_plan(results, direction)

            if plan:
                status = direction + " SETUP TO REVIEW"
            else:
                reasons.append(
                    "TRADE_PLAN_REJECTED_BY_RISK_FILTERS"
                )

    if not reasons:
        reasons.append("SETUP_PASSED_ALL_FILTERS")

    return (
        bias,
        status,
        long_points,
        short_points,
        plan,
        reasons
    )


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
    print("=" * 60)
    print("ARAD CRYPTO PRO V5.3 DEBUG")
    print("MTF + BOS + PULLBACK + RISK MANAGEMENT")
    print("ANALYSIS ONLY - NO ORDERS")
    print("=" * 60)

    contracts = get_contracts()
    tickers = get_tickers()

    if not contracts or not tickers:
        print("ERROR: MARKET DATA UNAVAILABLE")
        return

    ticker_map = {
        x.get("symbol"): x
        for x in tickers
        if x.get("symbol")
    }

    candidates = []

    for contract in contracts:
        symbol = contract.get("symbol")
        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        try:
            amount = float(ticker.get("amount24", 0))
            price = get_live_price(ticker)
        except (ValueError, TypeError):
            continue

        if amount <= 0 or price <= 0:
            continue

        candidates.append((symbol, amount))

    candidates.sort(key=lambda x: x[1], reverse=True)

    selected = candidates[:MAX_COINS]
    selected_names = {x[0] for x in selected}

    for symbol, amount in candidates:
        base = symbol.split("_")[0]

        if base in MEME_COINS and symbol not in selected_names:
            selected.append((symbol, amount))
            selected_names.add(symbol)

    print("Eligible contracts:", len(contracts))
    print("Selected symbols:", len(selected))

    confirmed_count = 0

    for symbol, _ in selected:
        print("\n" + "-" * 60)
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
            print("SYMBOL ERROR:", str(error)[:150])

    print("\n" + "=" * 60)
    print("V5.3 ANALYSIS COMPLETED")
    print("REVIEW SETUPS:", confirmed_count)
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 60)


if __name__ == "__main__":
    main()
