
import os
import time
import json
import html
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V4.2
# MEXC USDT PERPETUAL SCANNER
# SIGNALS ONLY - NO AUTOMATIC ORDERS
# TELEGRAM ENABLED
# ==========================================================

BASE_URL = "https://contract.mexc.com"
TELEGRAM_URL = "https://api.telegram.org"

MAX_TOP_COINS = 30
MIN_SCORE = 80
REQUEST_DELAY = 0.20
COOLDOWN_HOURS = 4

STATE_FILE = "signal_state.json"
REPORT_FILE = "arad_v42_report.txt"

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
        headers={"User-Agent": "ARAD-CRYPTO-PRO-V4.2"}
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"API failed ({path}): {exc}"
        ) from exc

    if isinstance(result, dict):
        if result.get("success") is False:
            raise RuntimeError(f"MEXC error: {result}")

        code = result.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(f"MEXC error: {result}")

    return result


# -------------------- TELEGRAM --------------------

def send_telegram(message):
    token = os.getenv("BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "BOT_TOKEN or CHAT_ID is missing in GitHub Secrets"
        )

    url = f"{TELEGRAM_URL}/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Telegram delivery failed: {exc}"
        ) from exc

    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected message: {result}")

    return True


# -------------------- INDICATORS --------------------

def ema(values, period):
    if len(values) < period:
        return None

    alpha = 2.0 / (period + 1)
    value = sum(values[:period]) / period

    for item in values[period:]:
        value = alpha * item + (1 - alpha) * value

    return value


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(changes)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    if avg_gain == 0:
        return 0.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(high, low, close, period=14):
    if len(close) < period + 1:
        return 0.0

    ranges = []

    for i in range(1, len(close)):
        ranges.append(max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        ))

    return sum(ranges[-period:]) / period


# -------------------- CANDLES --------------------

def get_candles(symbol, timeframe):

    intervals = {
        "15m": ("Min15", 900),
        "1h": ("Min60", 3600)
    }

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

    if not all(isinstance(x, list) for x in fields):
        return None

    count = min(len(x) for x in fields)
    candles = []

    for i in range(count):
        try:
            timestamp = int(fields[0][i])

            # Ignore unfinished candles.
            if timestamp + seconds > now:
                continue

            close = float(fields[1][i])
            high = float(fields[2][i])
            low = float(fields[3][i])
            volume = float(fields[4][i])

            if min(close, high, low) <= 0:
                continue

            candles.append(
                (timestamp, close, high, low, volume)
            )

        except (ValueError, TypeError, IndexError):
            continue

    candles.sort(key=lambda x: x[0])

    unique = {c[0]: c for c in candles}
    candles = [unique[k] for k in sorted(unique)]

    if len(candles) < 205:
        return None

    return {
        "close": [c[1] for c in candles],
        "high": [c[2] for c in candles],
        "low": [c[3] for c in candles],
        "volume": [c[4] for c in candles]
    }


# -------------------- ANALYSIS --------------------

def analyze(data):

    if not data:
        return None

    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    if len(close) < 205:
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
        macd_history.append(
            ema(part, 12) - ema(part, 26)
        )

    macd_line = macd_history[-1]
    macd_signal = ema(macd_history, 9)

    if macd_signal is None:
        return None

    macd_hist = macd_line - macd_signal

    avg_volume = sum(volume[-21:-1]) / 20

    volume_ratio = (
        volume[-1] / avg_volume
        if avg_volume > 0 else 0
    )

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
        "sentiment_score": round(
            100 * max(bull, bear) / 6
        )
    }


# -------------------- MARKET METRICS --------------------

def get_market_metrics(symbol):

    oi = None
    funding = None

    try:
        result = api_get(
            "/api/v1/contract/ticker",
            {"symbol": symbol}
        )

        data = result.get("data", {})

        if isinstance(data, list) and data:
            data = data[0]

        if isinstance(data, dict):
            if data.get("holdVol") is not None:
                oi = float(data["holdVol"])

            if data.get("fundingRate") is not None:
                funding = float(data["fundingRate"])

    except Exception as exc:
        print("Market metrics unavailable:", symbol, exc)

    return oi, funding


def get_buy_sell_pressure(symbol):

    try:
        result = api_get(
            "/api/v1/contract/deals/" + symbol,
            {"limit": 100}
        )

        deals = result.get("data", [])

        if not isinstance(deals, list) or not deals:
            return None

        buy = 0.0
        sell = 0.0

        for trade in deals:
            try:
                volume = float(trade.get("v", 0))
                side = int(trade.get("T", 0))
            except (ValueError, TypeError):
                continue

            if side == 1:
                buy += volume
            elif side == 2:
                sell += volume

        total = buy + sell

        if total <= 0:
            return None

        return {
            "buy_percent": 100 * buy / total,
            "sell_percent": 100 * sell / total
        }

    except Exception as exc:
        print("Pressure unavailable:", symbol, exc)
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

        # Both timeframes must agree.
        if not direction or trend(h1) != direction:
            return None

        expected = (
            "BULLISH" if direction == "LONG"
            else "BEARISH"
        )

        if (
            m15["sentiment"] != expected
            or h1["sentiment"] != expected
            or m15["sentiment_score"] < 65
            or h1["sentiment_score"] < 65
        ):
            return None

        if direction == "LONG":
            if not 50 <= m15["rsi"] <= 68:
                return None
        else:
            if not 32 <= m15["rsi"] <= 50:
                return None

        # Avoid excessively extended entries.
        if abs(m15["price"] - m15["ema21"]) > m15["atr"] * 1.5:
            return None

        oi, funding = get_market_metrics(symbol)
        pressure = get_buy_sell_pressure(symbol)

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
            if (
                direction == "LONG"
                and pressure["buy_percent"] > 55
            ):
                score += 5

            elif (
                direction == "SHORT"
                and pressure["sell_percent"] > 55
            ):
                score += 5

        score = min(score, 100)

        if score < MIN_SCORE or m15["atr"] <= 0:
            return None

        entry = m15["price"]
        risk = m15["atr"] * 1.5

        if direction == "LONG":
            sl = entry - risk
            targets = [
                entry + risk * x
                for x in (1, 1.5, 2, 2.5, 3)
            ]
        else:
            sl = entry + risk
            targets = [
                entry - risk * x
                for x in (1, 1.5, 2, 2.5, 3)
            ]

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
        and item.get("state", 0) == 0
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

    valid.sort(key=lambda x: x[1], reverse=True)

    memes = [
        symbol for symbol in sorted(MEME_COINS)
        if symbol in active
    ]

    top = [
        symbol for symbol, _ in valid[:MAX_TOP_COINS]
    ]

    # Meme coins plus top liquid coins.
    return list(dict.fromkeys(memes + top))


# -------------------- SIGNAL HISTORY --------------------

def load_state():

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except (OSError, ValueError):
        return {}


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

    os.replace(temp_file, STATE_FILE)


def is_duplicate(signal, state):

    key = (
        signal["symbol"] + "|" +
        signal["direction"]
    )

    previous = state.get(key)

    if previous is None:
        return False

    try:
        elapsed = time.time() - float(previous)
    except (TypeError, ValueError):
        return False

    return elapsed < COOLDOWN_HOURS * 3600


# -------------------- MESSAGE --------------------

def format_message(signal):

    direction = signal["direction"]

    emoji = "🟢" if direction == "LONG" else "🔴"

    pressure = signal["pressure"]

    if pressure:
        pressure_text = (
            f"Buy: {pressure['buy_percent']:.1f}% | "
            f"Sell: {pressure['sell_percent']:.1f}%"
        )
    else:
        pressure_text = "Unavailable"

    oi_text = (
        f"{signal['oi']:.4f}"
        if signal["oi"] is not None
        else "Unavailable"
    )

    funding_text = (
        f"{signal['funding']:.6f}"
        if signal["funding"] is not None
        else "Unavailable"
    )

    targets = "\n".join(
        f"TP{i+1}: <code>{price:.8g}</code>"
        for i, price in enumerate(signal["targets"])
    )

    return (
        f"{emoji} <b>ARAD CRYPTO PRO V4.2</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Symbol: <b>{html.escape(signal['symbol'])}</b>\n"
        f"Direction: <b>{direction}</b>\n"
        f"Score: <b>{signal['score']}/100</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Entry: <code>{signal['entry']:.8g}</code>\n"
        f"Stop Loss: <code>{signal['sl']:.8g}</code>\n"
        f"{targets}\n"
        f"━━━━━━━━━━━━━━\n"
        f"RSI 15m: {signal['rsi']:.2f}\n"
        f"Volume: {signal['volume']:.2f}x\n"
        f"Trend 15m: {signal['sentiment15']}/100\n"
        f"Trend 1h: {signal['sentiment1h']}/100\n"
        f"Open Interest: {oi_text}\n"
        f"Funding: {funding_text}\n"
        f"Buy/Sell: {pressure_text}\n"
        f"━━━━━━━━━━━━━━\n"
        f"<i>Signals only - No automatic orders</i>\n"
        f"UTC: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    )


# -------------------- MAIN --------------------

def main():

    print("ARAD CRYPTO PRO V4.2 STARTED")
    print("Mode: LIVE SIGNALS - NO ORDERS")
    print("Telegram: ENABLED")
    print("Minimum score:", MIN_SCORE)
    print("UTC:", datetime.now(timezone.utc).isoformat())

    # Fail early if Telegram configuration is missing.
    if not os.getenv("BOT_TOKEN") or not os.getenv("CHAT_ID"):
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID")

    symbols = get_symbols()

    print("Symbols to scan:", len(symbols))

    state = load_state()

    sent = 0
    duplicates = 0
    errors = 0

    with open(REPORT_FILE, "w", encoding="utf-8") as report:

        for index, symbol in enumerate(symbols, 1):

            print(f"[{index}/{len(symbols)}] Scanning {symbol}")

            try:
                signal = make_signal(symbol)

                if not signal:
                    print("No valid signal")
                    continue

                print(
                    f"Signal candidate: {symbol} "
                    f"{signal['direction']} "
                    f"Score={signal['score']}"
                )

                if is_duplicate(signal, state):
                    print("Duplicate skipped:", symbol)
                    duplicates += 1
                    continue

                message = format_message(signal)

                # Save only after successful Telegram delivery.
                send_telegram(message)

                key = (
                    signal["symbol"] + "|" +
                    signal["direction"]
                )

                state[key] = time.time()
                save_state(state)

                report.write(message + "\n\n")
                report.flush()

                sent += 1
                print("Telegram signal sent:", symbol)

            except Exception as exc:
                errors += 1
                print("SCAN ERROR:", symbol, exc)

            time.sleep(REQUEST_DELAY)

    print("\n========== SCAN FINISHED ==========")
    print("Symbols scanned:", len(symbols))
    print("Signals sent:", sent)
    print("Duplicates skipped:", duplicates)
    print("Errors:", errors)
    print("Report:", REPORT_FILE)


if __name__ == "__main__":
    main()
