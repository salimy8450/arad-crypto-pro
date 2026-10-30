
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# =====================================
# ARAD CRYPTO PRO V1
# Binance USDT-M Futures Signal Scanner
# =====================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BASE_URL = "https://fapi.binance.com"
SCAN_INTERVAL = 300
MAX_COINS = 100
MIN_SCORE = 75

MEME_COINS = {
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT",
    "BONKUSDT", "WIFUSDT", "FLOKIUSDT",
    "BOMEUSDT", "MEMEUSDT", "TURBOUSDT",
    "1000SATSUSDT", "1000CHEEMSUSDT"
}

sent_signals = {}


def api_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url, headers={"User-Agent": "ARAD-CRYPTO-PRO"}
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode())


def telegram_send(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message
    }).encode()

    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())
            return result.get("ok", False)
    except Exception as exc:
        print("Telegram error:", exc)
        return False


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value * multiplier) + (
            result * (1 - multiplier)
        )

    return result


def rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    if len(gains) < period:
        return 50

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def get_market_data(symbol, interval):
    candles = api_get(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": 120}
    )

    # Ignore the currently forming candle
    candles = candles[:-1]

    return {
        "close": [float(c[4]) for c in candles],
        "high": [float(c[2]) for c in candles],
        "low": [float(c[3]) for c in candles],
        "volume": [float(c[5]) for c in candles]
    }


def analyze(data):
    close = data["close"]
    high = data["high"]
    low = data["low"]
    volume = data["volume"]

    if len(close) < 60:
        return None

    e9 = ema(close, 9)
    e21 = ema(close, 21)
    e50 = ema(close, 50)
    current_rsi = rsi(close)

    true_ranges = []
    for i in range(1, len(close)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
        true_ranges.append(tr)

    atr = sum(true_ranges[-14:]) / 14

    avg_volume = sum(volume[-21:-1]) / 20
    volume_ratio = (
        volume[-1] / avg_volume if avg_volume else 0
    )

    price = close[-1]

    return {
        "price": price,
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "rsi": current_rsi,
        "atr": atr,
        "volume_ratio": volume_ratio
    }


def make_signal(symbol):
    try:
        m15 = analyze(get_market_data(symbol, "15m"))
        h1 = analyze(get_market_data(symbol, "1h"))

        if not m15 or not h1:
            return None

        directions = []

        for item in (m15, h1):
            if (
                item["ema9"] > item["ema21"] > item["ema50"]
                and item["price"] > item["ema21"]
            ):
                directions.append("LONG")

            elif (
                item["ema9"] < item["ema21"] < item["ema50"]
                and item["price"] < item["ema21"]
            ):
                directions.append("SHORT")

            else:
                return None

        if directions[0] != directions[1]:
            return None

        direction = directions[0]
        r = m15["rsi"]
        score = 65

        if direction == "LONG" and 50 <= r <= 70:
            score += 10
        elif direction == "SHORT" and 30 <= r <= 50:
            score += 10
        else:
            return None

        if m15["volume_ratio"] >= 1.2:
            score += 10

        if h1["volume_ratio"] >= 1.1:
            score += 5

        if symbol in MEME_COINS:
            score += 5

        if score < MIN_SCORE:
            return None

        entry = m15["price"]
        atr = m15["atr"]

        if atr <= 0:
            return None

        risk = atr * 1.5

        if direction == "LONG":
            sl = entry - risk
            targets = [
                entry + risk * 1.0,
                entry + risk * 1.5,
                entry + risk * 2.0,
                entry + risk * 2.5,
                entry + risk * 3.0
            ]
        else:
            sl = entry + risk
            targets = [
                entry - risk * 1.0,
                entry - risk * 1.5,
                entry - risk * 2.0,
                entry - risk * 2.5,
                entry - risk * 3.0
            ]

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "targets": targets,
            "score": score,
            "rsi": r,
            "volume": m15["volume_ratio"]
        }

    except Exception as exc:
        print("Analysis error:", symbol, exc)
        return None


def format_signal(signal):
    s = signal
    direction = "🟢 LONG" if s["direction"] == "LONG" else "🔴 SHORT"

    targets = "\n".join(
        f"TP{i+1}: {value:.8g}"
        for i, value in enumerate(s["targets"])
    )

    return (
        "🚀 ARAD CRYPTO PRO V1\n\n"
        f"ارز: {s['symbol']}\n"
        f"جهت: {direction}\n"
        f"ورود: {s['entry']:.8g}\n"
        f"حد ضرر: {s['sl']:.8g}\n\n"
        f"{targets}\n\n"
        f"قدرت سیگنال: {s['score']}%\n"
        f"RSI 15m: {s['rsi']:.1f}\n"
        f"حجم: {s['volume']:.2f}x\n"
        "تأیید روند: 15m + 1h\n"
        f"زمان: {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
        "⚠️ سیگنال تحلیلی است؛ قبل از ورود بررسی شود."
    )


def get_symbols():
    exchange = api_get("/fapi/v1/exchangeInfo")
    tickers = api_get("/fapi/v1/ticker/24hr")

    active = {
        item["symbol"]
        for item in exchange["symbols"]
        if item["status"] == "TRADING"
        and item["quoteAsset"] == "USDT"
        and item["contractType"] == "PERPETUAL"
    }

    ranked = [
        item for item in tickers
        if item["symbol"] in active
    ]

    ranked.sort(
        key=lambda x: float(x.get("quoteVolume", 0)),
        reverse=True
    )

    top_symbols = [
        item["symbol"] for item in ranked[:MAX_COINS]
    ]

    # Give priority to meme coins
    return list(dict.fromkeys(
        list(MEME_COINS) + top_symbols
    ))


def main():
    print("ARAD CRYPTO PRO V1 started.")

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing.")

    while True:
        try:
            symbols = get_symbols()
            print("Scanning", len(symbols), "symbols")

            for symbol in symbols:
                signal = make_signal(symbol)

                if not signal:
                    continue

                key = (
                    symbol,
                    signal["direction"]
                )

                # Avoid repeating the same direction
                if key in sent_signals:
                    continue

                message = format_signal(signal)

                if telegram_send(message):
                    sent_signals[key] = time.time()
                    print("Signal sent:", symbol, key[1])

                time.sleep(0.25)

            print("Scan completed.")

        except Exception as exc:
            print("Scanner error:", exc)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
