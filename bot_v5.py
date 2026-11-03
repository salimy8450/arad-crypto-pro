
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5 - HUMAN STYLE ANALYSIS ENGINE
# PHASE 1: MARKET DATA + MULTI-TIMEFRAME STRUCTURE
# NO ORDERS - NO TRADING
# ==========================================================

BASE_URL = "https://contract.mexc.com"

MAX_COINS = 15
REQUEST_DELAY = 0.15
CANDLE_LIMIT = 240

TIMEFRAMES = {
    "5m": "Min5",
    "15m": "Min15",
    "1h": "Min60",
    "4h": "Hour4",
}

EXCLUDED_WORDS = (
    "STOCK", "ETF", "OIL", "GOLD", "SILVER",
    "INDEX", "NASDAQ", "SP500", "US30"
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

        if not result.get("success", False):
            return None

        return result.get("data")

    except Exception as error:
        print("API ERROR:", str(error)[:150])
        return None


def get_contracts():
    data = api_get("/api/v1/contract/detail")

    if not isinstance(data, list):
        return []

    contracts = []

    for item in data:
        symbol = item.get("symbol", "")
        base = item.get("baseCoin", "")
        quote = item.get("quoteCoin", "")
        settle = item.get("settleCoin", "")

        if not symbol.endswith("_USDT"):
            continue

        if quote != "USDT" or settle != "USDT":
            continue

        if not base or not base.replace("_", "").isalnum():
            continue

        description = (
            base + " " +
            item.get("displayNameEn", "")
        ).upper()

        if any(word in description for word in EXCLUDED_WORDS):
            continue

        contracts.append(item)

    return contracts


def get_tickers():
    data = api_get("/api/v1/contract/ticker")

    if isinstance(data, dict):
        return [data]

    return data if isinstance(data, list) else []


def get_candles(symbol, interval):
    data = api_get(
        "/api/v1/contract/kline/" + symbol,
        {"interval": interval}
    )

    if not isinstance(data, dict):
        return None

    keys = ("time", "open", "high", "low", "close", "vol")

    if not all(key in data for key in keys):
        return None

    candles = []

    count = min(len(data[key]) for key in keys)

    now = int(time.time())

    seconds = {
        "Min5": 300,
        "Min15": 900,
        "Min60": 3600,
        "Hour4": 14400,
    }[interval]

    for i in range(count):
        candle_time = int(data["time"][i])

        # Ignore unfinished candles
        if candle_time + seconds > now:
            continue

        candle = {
            "time": candle_time,
            "open": float(data["open"][i]),
            "high": float(data["high"][i]),
            "low": float(data["low"][i]),
            "close": float(data["close"][i]),
            "volume": float(data["vol"][i]),
        }

        if candle["close"] > 0:
            candles.append(candle)

    if len(candles) < 205:
        return None

    return candles[-CANDLE_LIMIT:]


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

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

        true_range = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        ranges.append(true_range)

    return sum(ranges[-period:]) / period


def find_swings(candles, left=3, right=3):
    highs = []
    lows = []

    for i in range(left, len(candles) - right):
        high = candles[i]["high"]
        low = candles[i]["low"]

        if all(
            high > candles[j]["high"]
            for j in range(i - left, i)
        ) and all(
            high >= candles[j]["high"]
            for j in range(i + 1, i + right + 1)
        ):
            highs.append(high)

        if all(
            low < candles[j]["low"]
            for j in range(i - left, i)
        ) and all(
            low <= candles[j]["low"]
            for j in range(i + 1, i + right + 1)
        ):
            lows.append(low)

    return highs, lows


def analyze_timeframe(candles):
    closes = [x["close"] for x in candles]
    volumes = [x["volume"] for x in candles]

    current = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    rsi = calculate_rsi(closes, 14)
    atr = calculate_atr(candles)

    if None in (e9, e21, e50, e200, rsi, atr):
        return None

    average_volume = sum(volumes[-21:-1]) / 20

    volume_ratio = (
        volumes[-1] / average_volume
        if average_volume > 0 else 0
    )

    swing_highs, swing_lows = find_swings(candles[-100:])

    resistance = (
        max(swing_highs[-5:])
        if swing_highs else None
    )

    support = (
        min(swing_lows[-5:])
        if swing_lows else None
    )

    bullish = (
        e9 > e21 > e50 > e200 and
        current > e21
    )

    bearish = (
        e9 < e21 < e50 < e200 and
        current < e21
    )

    if bullish:
        trend = "BULLISH"
    elif bearish:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    previous_highs, previous_lows = find_swings(
        candles[-80:-1]
    )

    bos = "NONE"

    if previous_highs and current > max(previous_highs[-3:]):
        bos = "BULLISH_BOS"

    elif previous_lows and current < min(previous_lows[-3:]):
        bos = "BEARISH_BOS"

    return {
        "price": current,
        "trend": trend,
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "rsi": rsi,
        "atr": atr,
        "volume_ratio": volume_ratio,
        "support": support,
        "resistance": resistance,
        "bos": bos,
        "candles": len(candles),
    }


def analyze_symbol(symbol):
    results = {}

    for name, interval in TIMEFRAMES.items():
        candles = get_candles(symbol, interval)

        time.sleep(REQUEST_DELAY)

        if not candles:
            print(symbol, name, "DATA INCOMPLETE")
            return None

        result = analyze_timeframe(candles)

        if not result:
            return None

        results[name] = result

    return results


def market_decision(results):
    h4 = results["4h"]
    h1 = results["1h"]
    m15 = results["15m"]
    m5 = results["5m"]

    long_points = 0
    short_points = 0

    # Main trend
    if h4["trend"] == "BULLISH":
        long_points += 3
    elif h4["trend"] == "BEARISH":
        short_points += 3

    if h1["trend"] == "BULLISH":
        long_points += 3
    elif h1["trend"] == "BEARISH":
        short_points += 3

    # Structure confirmation
    if m15["bos"] == "BULLISH_BOS":
        long_points += 2
    elif m15["bos"] == "BEARISH_BOS":
        short_points += 2

    # Entry timeframe
    if m5["trend"] == "BULLISH":
        long_points += 1
    elif m5["trend"] == "BEARISH":
        short_points += 1

    # Volume confirmation
    if m15["volume_ratio"] >= 1.2:
        if m15["price"] > m15["ema21"]:
            long_points += 1
        elif m15["price"] < m15["ema21"]:
            short_points += 1

    # Contradiction check
    if long_points >= 7 and short_points <= 2:
        decision = "LONG BIAS"

    elif short_points >= 7 and long_points <= 2:
        decision = "SHORT BIAS"

    else:
        decision = "NO TRADE"

    return decision, long_points, short_points


def main():
    print("=" * 55)
    print("ARAD CRYPTO PRO V5")
    print("HUMAN STYLE ANALYSIS ENGINE")
    print("MODE: ANALYSIS ONLY - NO ORDERS")
    print("=" * 55)

    contracts = get_contracts()
    tickers = get_tickers()

    if not contracts or not tickers:
        print("ERROR: Cannot load market data")
        return

    ticker_map = {
        item.get("symbol"): item
        for item in tickers
        if item.get("symbol")
    }

    candidates = []

    for contract in contracts:
        symbol = contract.get("symbol")
        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        try:
            amount = float(ticker.get("amount24", 0))
            price = float(ticker.get("lastPrice", 0))
        except (TypeError, ValueError):
            continue

        if amount <= 0 or price <= 0:
            continue

        candidates.append((symbol, amount))

    candidates.sort(key=lambda x: x[1], reverse=True)

    selected = candidates[:MAX_COINS]

    # Add selected meme coins if available
    selected_symbols = {x[0] for x in selected}

    for symbol, amount in candidates:
        base = symbol.split("_")[0]

        if base in MEME_COINS and symbol not in selected_symbols:
            selected.append((symbol, amount))
            selected_symbols.add(symbol)

    print("Contracts available:", len(contracts))
    print("Symbols selected:", len(selected))
    print()

    for symbol, _ in selected:
        print("-" * 55)
        print("ANALYZING:", symbol)

        try:
            results = analyze_symbol(symbol)

            if not results:
                print("RESULT: INSUFFICIENT DATA")
                continue

            decision, long_score, short_score = market_decision(
                results
            )

            print("PRICE:", results["15m"]["price"])
            print("DECISION:", decision)
            print("LONG POINTS:", long_score)
            print("SHORT POINTS:", short_score)

            for tf in ("4h", "1h", "15m", "5m"):
                item = results[tf]

                print(
                    tf,
                    "| Trend:", item["trend"],
                    "| RSI:", round(item["rsi"], 2),
                    "| Volume:", round(item["volume_ratio"], 2),
                    "| BOS:", item["bos"]
                )

                print(
                    "Support:", item["support"],
                    "| Resistance:", item["resistance"]
                )

        except Exception as error:
            print("SYMBOL ERROR:", str(error)[:150])

    print()
    print("=" * 55)
    print("V5 PHASE 1 SCAN COMPLETED")
    print("UTC:", datetime.now(timezone.utc).isoformat())
    print("NO ORDERS WERE PLACED")
    print("=" * 55)


if __name__ == "__main__":
    main()
