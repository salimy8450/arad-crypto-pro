import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V4.1 TEST
# MEXC USDT PERPETUAL - DRY RUN ONLY
# NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://contract.mexc.com"
MAX_COINS = 8
MIN_SCORE = 75
REQUEST_DELAY = 0.25
OI_FILE = "oi_test_state.json"
REPORT_FILE = "arad_v41_test_report.txt"

MEME_COINS = {
    "DOGE_USDT", "SHIB_USDT", "PEPE_USDT",
    "BONK_USDT", "WIF_USDT", "FLOKI_USDT",
    "BOME_USDT", "MEME_USDT", "TURBO_USDT"
}


# -------------------- API --------------------

def api_get(path, params=None):
    query = urllib.parse.urlencode(params or {})
    url = BASE_URL + path
    if query:
        url += "?" + query

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ARAD-CRYPTO-PRO-V4.1-TEST"}
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"API request failed ({path}): {exc}") from exc

    if isinstance(result, dict):
        if result.get("success") is False:
            raise RuntimeError(f"MEXC API error ({path}): {result}")
        code = result.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(f"MEXC API error ({path}): {result}")

    return result


# -------------------- INDICATORS --------------------

def ema(values, period):
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(close)):
        true_ranges.append(max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        ))

    return sum(true_ranges[-period:]) / period


# -------------------- CANDLE DATA --------------------

def get_candles(symbol, timeframe):
    intervals = {
        "15m": ("Min15", 900),
        "1h": ("Min60", 3600)
    }
    if timeframe not in intervals:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    interval, seconds = intervals[timeframe]
    now = int(time.time())

    result = api_get(
        "/api/v1/contract/kline/" + symbol,
        {
            "interval": interval,
            "start": now - 240 * seconds,
            "end": now
        }
    )

    data = result.get("data", {})
    if not isinstance(data, dict):
        return None

    fields = [
        data.get("time", []),
        data.get("close", []),
        data.get("high", []),
        data.get("low", []),
        data.get("vol", [])
    ]

    if not all(isinstance(field, list) for field in fields):
        return None

    count = min(len(field) for field in fields)
    candles = []

    for i in range(count):
        try:
            timestamp = int(fields[0][i])
            # Exclude the currently forming candle.
            if timestamp + seconds > now:
                continue

            close_price = float(fields[1][i])
            high_price = float(fields[2][i])
            low_price = float(fields[3][i])
            volume = float(fields[4][i])

            if close_price <= 0 or high_price <= 0 or low_price <= 0:
                continue

            candles.append((
                timestamp, close_price, high_price, low_price, volume
            ))
        except (TypeError, ValueError, IndexError):
            continue

    # IMPORTANT: sort by candle timestamp, never by price.
    candles.sort(key=lambda candle: candle[0])

    # Remove duplicate timestamps if the API returns any.
    unique = {}
    for candle in candles:
        unique[candle[0]] = candle
    candles = [unique[key] for key in sorted(unique)]

    if len(candles) < 205:
        return None

    return {
        "close": [candle[1] for candle in candles],
        "high": [candle[2] for candle in candles],
        "low": [candle[3] for candle in candles],
        "volume": [candle[4] for candle in candles]
    }


# -------------------- TECHNICAL ANALYSIS --------------------

def analyze(data):
    if not data:
        return None

    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    if len(close) < 205 or len(high) != len(close) or len(low) != len(close):
        return None

    e9 = ema(close, 9)
    e21 = ema(close, 21)
    e50 = ema(close, 50)
    e200 = ema(close, 200)

    current_rsi = rsi(close)
    current_atr = atr(high, low, close)

    macd_history = []
    for i in range(35, len(close) + 1):
        part = close[:i]
        macd_history.append(ema(part, 12) - ema(part, 26))

    macd_line = macd_history[-1]
    macd_signal = ema(macd_history, 9)
    if macd_signal is None:
        return None
    macd_hist = macd_line - macd_signal

    avg_volume = sum(volume[-21:-1]) / 20
    volume_ratio = volume[-1] / avg_volume if avg_volume > 0 else 0.0

    bull = 0
    bear = 0

    if e9 > e21:
        bull += 1
    elif e9 < e21:
        bear += 1

    if e21 > e50:
        bull += 1
    elif e21 < e50:
        bear += 1

    if close[-1] > e200:
        bull += 2
    elif close[-1] < e200:
        bear += 2

    if macd_hist > 0:
        bull += 1
    elif macd_hist < 0:
        bear += 1

    if current_rsi >= 52:
        bull += 1
    elif current_rsi <= 48:
        bear += 1

    sentiment = (
        "BULLISH" if bull > bear
        else "BEARISH" if bear > bull
        else "NEUTRAL"
    )

    return {
        "price": close[-1],
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "rsi": current_rsi,
        "atr": current_atr,
        "macd": macd_hist,
        "volume_ratio": volume_ratio,
        "sentiment": sentiment,
        "sentiment_score": round(100 * max(bull, bear) / 6)
    }


# -------------------- MARKET DATA --------------------

def get_open_interest(symbol):
    try:
        result = api_get("/api/v1/contract/open_interest/" + symbol)
        data = result.get("data", {})
        if isinstance(data, dict) and data.get("holdVol") is not None:
            return float(data["holdVol"])
    except Exception as exc:
        print("OI unavailable:", symbol, exc)
    return None


def get_funding_rate(symbol):
    try:
        result = api_get("/api/v1/contract/funding_rate/" + symbol)
        data = result.get("data", {})
        if isinstance(data, dict) and data.get("fundingRate") is not None:
            return float(data["fundingRate"])
    except Exception as exc:
        print("Funding unavailable:", symbol, exc)
    return None


def get_buy_sell_pressure(symbol):
    try:
        result = api_get(
            "/api/v1/contract/deals/" + symbol,
            {"limit": 100}
        )
        deals = result.get("data", [])
        if not isinstance(deals, list) or not deals:
            return None

        buy_volume = 0.0
        sell_volume = 0.0

        for trade in deals:
            volume = float(trade.get("v", 0) or 0)
            side = trade.get("T")
            try:
                side = int(side)
            except (TypeError, ValueError):
                continue

            if side == 1:
                buy_volume += volume
            elif side == 2:
                sell_volume += volume

        total = buy_volume + sell_volume
        if total <= 0:
            return None

        return {
            "buy": buy_volume,
            "sell": sell_volume,
            "buy_percent": 100.0 * buy_volume / total,
            "sell_percent": 100.0 * sell_volume / total
        }
    except Exception as exc:
        print("Buy/Sell unavailable:", symbol, exc)
        return None


def get_oi_change(symbol):
    """Change since the previous successful scan, not a fixed 15m change."""
    try:
        state = {}
        if os.path.exists(OI_FILE):
            with open(OI_FILE, "r", encoding="utf-8") as file:
                loaded = json.load(file)
                if isinstance(loaded, dict):
                    state = loaded

        current = get_open_interest(symbol)
        if current is None or current <= 0:
            return None

        previous = state.get(symbol)
        state[symbol] = current

        with open(OI_FILE, "w", encoding="utf-8") as file:
            json.dump(state, file, indent=2)

        if previous is None or float(previous) <= 0:
            return None

        return ((current - float(previous)) / float(previous)) * 100.0
    except Exception as exc:
        print("OI change error:", symbol, exc)
        return None


# -------------------- SIGNAL GENERATOR --------------------

def make_signal(symbol):
    try:
        data15 = get_candles(symbol, "15m")
        time.sleep(REQUEST_DELAY)
        data1h = get_candles(symbol, "1h")

        m15 = analyze(data15)
        h1 = analyze(data1h)
        if not m15 or not h1:
            return None

        def trend(item):
            if (
                item["ema9"] > item["ema21"] > item["ema50"]
                and item["price"] > item["ema21"]
            ):
                return "LONG"
            if (
                item["ema9"] < item["ema21"] < item["ema50"]
                and item["price"] < item["ema21"]
            ):
                return "SHORT"
            return None

        direction = trend(m15)
        if not direction or trend(h1) != direction:
            return None

        expected = "BULLISH" if direction == "LONG" else "BEARISH"
        if (
            m15["sentiment"] != expected
            or h1["sentiment"] != expected
            or m15["sentiment_score"] < 65
            or h1["sentiment_score"] < 65
        ):
            return None

        if direction == "LONG" and not 50 <= m15["rsi"] <= 70:
            return None
        if direction == "SHORT" and not 30 <= m15["rsi"] <= 50:
            return None

        # Auxiliary market data. OI is sampled once and never double-counted.
        oi = get_open_interest(symbol)
        funding = get_funding_rate(symbol)
        pressure = get_buy_sell_pressure(symbol)
        oi_change = get_oi_change(symbol)

        score = 65

        if m15["volume_ratio"] >= 1.2:
            score += 5
        if h1["volume_ratio"] >= 1.1:
            score += 5
        if m15["sentiment_score"] >= 80:
            score += 5
        if h1["sentiment_score"] >= 80:
            score += 5

        if pressure:
            if direction == "LONG" and pressure["buy_percent"] > 55:
                score += 5
            elif direction == "SHORT" and pressure["sell_percent"] > 55:
                score += 5

        # OI change is displayed for observation only. Rising OI alone
        # does not tell whether new positions are long or short.
        score = min(score, 100)

        if score < MIN_SCORE or m15["atr"] <= 0:
            return None

        entry = m15["price"]
        risk = m15["atr"] * 1.5

        if direction == "LONG":
            sl = entry - risk
            targets = [entry + risk * multiple for multiple in (1, 1.5, 2, 2.5, 3)]
        else:
            sl = entry + risk
            targets = [entry - risk * multiple for multiple in (1, 1.5, 2, 2.5, 3)]

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "targets": targets,
            "score": score,
            "rsi": m15["rsi"],
            "volume": m15["volume_ratio"],
            "sentiment15": m15["sentiment_score"],
            "sentiment1h": h1["sentiment_score"],
            "oi": oi,
            "oi_change": oi_change,
            "funding": funding,
            "pressure": pressure
        }

    except Exception as exc:
        print("Signal error:", symbol, exc)
        return None


# -------------------- SYMBOL DISCOVERY --------------------

def get_symbols():
    result = api_get("/api/v1/contract/detail")
    contracts = result.get("data", [])
    if not isinstance(contracts, list):
        raise RuntimeError("Invalid contract list")

    active = {
        item["symbol"]
        for item in contracts
        if item.get("quoteCoin") == "USDT"
        and item.get("settleCoin") == "USDT"
        and item.get("futureType") == 1
        and item.get("symbol")
    }

    tickers_result = api_get("/api/v1/contract/ticker")
    tickers = tickers_result.get("data", [])
    if not isinstance(tickers, list):
        raise RuntimeError("Invalid ticker data")

    valid = []
    for item in tickers:
        if item.get("symbol") not in active:
            continue
        try:
            amount = float(item.get("amount24") or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            valid.append((item["symbol"], amount))

    valid.sort(key=lambda pair: pair[1], reverse=True)
    memes = [symbol for symbol in sorted(MEME_COINS) if symbol in active]
    top = [symbol for symbol, _ in valid[:MAX_COINS]]

    # Prioritize the selected meme coins, then fill remaining slots by volume.
    ordered = list(dict.fromkeys(memes + top))
    return ordered[:MAX_COINS]


# -------------------- REPORT --------------------

def format_report(signal):
    pressure = signal["pressure"]
    if pressure:
        pressure_text = (
            f"Buy: {pressure['buy_percent']:.1f}% | "
            f"Sell: {pressure['sell_percent']:.1f}%"
        )
    else:
        pressure_text = "Unavailable"

    oi_text = f"{signal['oi']:.4f}" if signal["oi"] is not None else "Unavailable"
    oi_change_text = (
        f"{signal['oi_change']:+.2f}% since previous scan"
        if signal["oi_change"] is not None else "Not enough history"
    )
    funding_text = (
        f"{signal['funding']:.6f}"
        if signal["funding"] is not None else "Unavailable"
    )

    targets = "\n".join(
        f"TP{i + 1}: {price:.8g}"
        for i, price in enumerate(signal["targets"])
    )

    return (
        "\n================================\n"
        "ARAD CRYPTO PRO V4.1 TEST\n"
        "================================\n"
        f"Symbol: {signal['symbol']}\n"
        f"Direction: {signal['direction']}\n"
        f"Entry: {signal['entry']:.8g}\n"
        f"Stop Loss: {signal['sl']:.8g}\n"
        f"{targets}\n"
        "--------------------------------\n"
        f"Score: {signal['score']}/100 (technical filter score, not win probability)\n"
        f"RSI 15m: {signal['rsi']:.2f}\n"
        f"Volume ratio: {signal['volume']:.2f}x\n"
        f"Sentiment 15m: {signal['sentiment15']}\n"
        f"Sentiment 1h: {signal['sentiment1h']}\n"
        f"Open Interest: {oi_text}\n"
        f"OI change: {oi_change_text}\n"
        f"Funding Rate: {funding_text}\n"
        f"Buy/Sell: {pressure_text}\n"
        "================================\n"
    )


# -------------------- MAIN TEST --------------------

def main():
    print("\nARAD CRYPTO PRO V4.1 TEST STARTED")
    print("Mode: TEST ONLY - NO ORDERS - NO TELEGRAM")
    print("UTC:", datetime.now(timezone.utc).isoformat())

    symbols = get_symbols()
    print("Symbols found:", len(symbols), symbols)

    signals_found = 0
    errors = 0

    with open(REPORT_FILE, "w", encoding="utf-8") as report:
        for index, symbol in enumerate(symbols, 1):
            print(f"\n[{index}/{len(symbols)}] Testing {symbol}")
            try:
                signal = make_signal(symbol)
                if signal:
                    output = format_report(signal)
                    print(output)
                    report.write(output)
                    report.flush()
                    signals_found += 1
                else:
                    print("No valid signal")
            except Exception as exc:
                errors += 1
                print("TEST ERROR:", symbol, exc)

            time.sleep(REQUEST_DELAY)

    print("\n========== TEST FINISHED ==========")
    print("Signals found:", signals_found)
    print("Errors:", errors)
    print("Report:", REPORT_FILE)


if __name__ == "__main__":
    main()
