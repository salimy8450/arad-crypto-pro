
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.2
# MULTI-TIMEFRAME MARKET STRUCTURE ENGINE
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM SIGNALS
# ==========================================================

BASE_URL = "https://contract.mexc.com"

MAX_COINS = 20
CANDLE_LIMIT = 240
REQUEST_DELAY = 0.12
MIN_VOLUME_RATIO = 0.80

TIMEFRAMES = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
}

# Known non-crypto underlyings / tokenized stocks and commodities.
EXCLUDED_BASES = {
    "SOXL", "SOXS", "TESLA", "TSLA", "KORU",
    "MUSTOCK", "SNDKSTOCK", "USOIL", "UKOIL",
    "GOLD", "SILVER", "SPY", "QQQ", "NVDA",
    "AAPL", "AMZN", "META", "MSFT", "GOOGL",
    "COIN", "MSTR", "NFLX", "AMD", "INTC",
    "NVD", "TQQQ", "SQQQ", "UVXY"
}

EXCLUDED_WORDS = (
    "STOCK", "EQUITY", "ETF", "OIL",
    "GOLD", "SILVER", "INDEX", "NASDAQ",
    "NYSE", "COMMODITY"
)

MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF",
    "FLOKI", "BOME", "MEME", "TURBO"
}


def api_get(path, params=None):
    url = BASE_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ARAD-CRYPTO-PRO-V5"}
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
            base + " " +
            str(item.get("displayNameEn", ""))
        ).upper()

        if any(word in description for word in EXCLUDED_WORDS):
            continue

        # Do not include inactive contracts when state is supplied.
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
        timestamp = int(data["time"][i])

        # Only closed candles.
        if timestamp + seconds > now:
            continue

        try:
            candle = {
                "time": timestamp,
                "open": float(data["open"][i]),
                "high": float(data["high"][i]),
                "low": float(data["low"][i]),
                "close": float(data["close"][i]),
                "volume": float(data["vol"][i])
            }
        except (ValueError, TypeError):
            continue

        if (
            candle["close"] <= 0
            or candle["high"] < candle["low"]
            or candle["volume"] < 0
        ):
            continue

        candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    if len(candles) < 205:
        return None

    return candles[-CANDLE_LIMIT:]


def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = (
            value * multiplier +
            result * (1 - multiplier)
        )

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


def find_swings(candles, left=3, right=3):
    highs = []
    lows = []

    for i in range(left, len(candles) - right):
        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = (
            all(high > candles[j]["high"]
                for j in range(i-left, i))
            and
            all(high >= candles[j]["high"]
                for j in range(i+1, i+right+1))
        )

        is_low = (
            all(low < candles[j]["low"]
                for j in range(i-left, i))
            and
            all(low <= candles[j]["low"]
                for j in range(i+1, i+right+1))
        )

        if is_high:
            highs.append({
                "price": high,
                "time": candles[i]["time"]
            })

        if is_low:
            lows.append({
                "price": low,
                "time": candles[i]["time"]
            })

    return highs, lows


def nearest_levels(candles, price):
    highs, lows = find_swings(candles[-120:])

    # Nearest confirmed swing resistance above price.
    above = [
        x["price"] for x in highs
        if x["price"] > price
    ]

    # Nearest confirmed swing support below price.
    below = [
        x["price"] for x in lows
        if x["price"] < price
    ]

    resistance = min(above) if above else None
    support = max(below) if below else None

    return support, resistance


def detect_structure(candles):
    # Recent closed candle and previously confirmed swings.
    last = candles[-1]

    previous = candles[-100:-4]
    highs, lows = find_swings(previous)

    if not highs or not lows:
        return "NONE", "NONE"

    recent_high = highs[-1]["price"]
    recent_low = lows[-1]["price"]

    bos = "NONE"
    sweep = "NONE"

    if last["close"] > recent_high:
        bos = "BULLISH_BOS"

    elif last["close"] < recent_low:
        bos = "BEARISH_BOS"

    # Possible false-break / liquidity sweep.
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

    return bos, sweep


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
    bos, sweep = detect_structure(candles)

    return {
        "price": price,
        "trend": trend,
        "rsi": rsi,
        "atr": atr,
        "ema21": e21,
        "volume_ratio": volume_ratio,
        "support": support,
        "resistance": resistance,
        "bos": bos,
        "sweep": sweep
    }


def get_live_price(ticker):
    try:
        return float(ticker.get("lastPrice", 0))
    except (ValueError, TypeError):
        return 0


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

    live_price = get_live_price(ticker)

    if live_price <= 0:
        print(symbol, "LIVE PRICE UNAVAILABLE")
        return None

    results["live_price"] = live_price
    return results


def make_decision(results):
    h4 = results["4h"]
    h1 = results["1h"]
    m15 = results["15m"]
    m5 = results["5m"]

    long_points = 0
    short_points = 0

    # Higher timeframe direction.
    if h4["trend"] == "BULLISH":
        long_points += 3
    elif h4["trend"] == "BEARISH":
        short_points += 3

    if h1["trend"] == "BULLISH":
        long_points += 3
    elif h1["trend"] == "BEARISH":
        short_points += 3

    # Structure on 15m.
    if m15["bos"] == "BULLISH_BOS":
        long_points += 2
    elif m15["bos"] == "BEARISH_BOS":
        short_points += 2

    # Entry confirmation on 5m.
    if m5["bos"] == "BULLISH_BOS":
        long_points += 2
    elif m5["bos"] == "BEARISH_BOS":
        short_points += 2

    # Sweeps are warnings, not automatic trade signals.
    if m5["sweep"] == "BEARISH_SWEEP":
        long_points = max(0, long_points - 2)

    elif m5["sweep"] == "BULLISH_SWEEP":
        short_points = max(0, short_points - 2)

    bias = "NO TRADE"

    if long_points >= 7 and short_points <= 2:
        bias = "LONG BIAS"

    elif short_points >= 7 and long_points <= 2:
        bias = "SHORT BIAS"

    # Strict entry readiness: all timeframes must agree.
    entry_status = "NOT CONFIRMED"

    if (
        bias == "LONG BIAS"
        and h4["trend"] == "BULLISH"
        and h1["trend"] == "BULLISH"
        and m15["trend"] == "BULLISH"
        and m5["trend"] == "BULLISH"
        and m5["volume_ratio"] >= MIN_VOLUME_RATIO
        and m5["bos"] == "BULLISH_BOS"
    ):
        entry_status = "LONG SETUP TO REVIEW"

    elif (
        bias == "SHORT BIAS"
        and h4["trend"] == "BEARISH"
        and h1["trend"] == "BEARISH"
        and m15["trend"] == "BEARISH"
        and m5["trend"] == "BEARISH"
        and m5["volume_ratio"] >= MIN_VOLUME_RATIO
        and m5["bos"] == "BEARISH_BOS"
    ):
        entry_status = "SHORT SETUP TO REVIEW"

    return bias, entry_status, long_points, short_points


def print_levels(name, data):
    support = (
        f'{data["support"]:.10g}'
        if data["support"] is not None else "N/A"
    )

    resistance = (
        f'{data["resistance"]:.10g}'
        if data["resistance"] is not None else "N/A"
    )

    print(
        name,
        "| Trend:", data["trend"],
        "| RSI:", round(data["rsi"], 2),
        "| Vol:", round(data["volume_ratio"], 2),
        "| BOS:", data["bos"],
        "| Sweep:", data["sweep"]
    )

    print(
        "Support:", support,
        "| Resistance:", resistance
    )


def main():
    print("=" * 60)
    print("ARAD CRYPTO PRO V5.2")
    print("HUMAN STYLE MARKET ANALYSIS")
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

    for symbol, _ in selected:
        print("\n" + "-" * 60)
        print("ANALYZING:", symbol)

        try:
            ticker = ticker_map.get(symbol)

            results = analyze_symbol(symbol, ticker)

            if not results:
                print("RESULT: INSUFFICIENT DATA")
                continue

            bias, entry_status, long_score, short_score = (
                make_decision(results)
            )

            live = results["live_price"]
            reference = results["15m"]["price"]

            difference = (
                abs(live - reference) / reference * 100
                if reference else 0
            )

            print("LIVE PRICE:", live)
            print("LAST CLOSED 15M:", reference)
            print("PRICE DIFFERENCE %:", round(difference, 3))

            print("MARKET BIAS:", bias)
            print("ENTRY STATUS:", entry_status)
            print("LONG POINTS:", long_score)
            print("SHORT POINTS:", short_score)

            for tf in ("4h", "1h", "15m", "5m"):
                print_levels(tf, results[tf])

            # Large differences require a fresh review.
            if difference > 0.50:
                print("WARNING: LIVE PRICE MOVED - RECHECK REQUIRED")

        except Exception as error:
            print("SYMBOL ERROR:", str(error)[:150])

    print("\n" + "=" * 60)
    print("V5.2 ANALYSIS COMPLETED")
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 60)


if __name__ == "__main__":
    main()
