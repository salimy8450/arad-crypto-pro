
import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================
# ARAD CRYPTO PRO V2
# Bybit USDT Perpetual Market Scanner
# Signals only - No automatic trading
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BASE_URL = "https://api.bybit.com"

MAX_COINS = 100
MIN_SCORE = 75
REQUEST_DELAY = 0.20
STATE_FILE = "signal_state.json"
COOLDOWN_HOURS = 4

MEME_COINS = {
    "DOGEUSDT", "SHIBUSDT", "PEPEUSDT",
    "BONKUSDT", "WIFUSDT", "FLOKIUSDT",
    "BOMEUSDT", "MEMEUSDT", "TURBOUSDT",
    "1000SATSUSDT", "1000CHEEMSUSDT"
}


# ---------- HTTP REQUEST ----------

def api_get(path, params=None):
    query = urllib.parse.urlencode(params or {})
    url = BASE_URL + path

    if query:
        url += "?" + query

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ARAD-CRYPTO-PRO/2.0"}
            )

            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.loads(response.read().decode())

            if result.get("retCode", 0) != 0:
                raise RuntimeError(
                    "Bybit API error: " + str(result)
                )

            return result

        except urllib.error.HTTPError as exc:
            # Do not retry permanent access restrictions
            if exc.code in (403, 451):
                raise RuntimeError(
                    f"Bybit access restricted: HTTP {exc.code}"
                ) from exc

            print("API attempt", attempt + 1, "failed:", exc)

            if attempt == 2:
                raise

            time.sleep(2 * (attempt + 1))

        except Exception as exc:
            print("API attempt", attempt + 1, "failed:", exc)

            if attempt == 2:
                raise

            time.sleep(2 * (attempt + 1))


# ---------- TELEGRAM ----------

def telegram_send(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message
    }).encode()

    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode())

        if not result.get("ok"):
            print("Telegram rejected message:", result)
            return False

        return True

    except Exception as exc:
        print("Telegram error:", exc)
        return False


# ---------- INDICATORS ----------

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            value * multiplier
            + result * (1 - multiplier)
        )

    return result


def rsi(values, period=14):
    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    if len(changes) < period:
        return 50

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    if avg_gain == 0:
        return 0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ---------- MARKET DATA ----------

def get_market_data(symbol, interval):
    interval_map = {
        "15m": "15",
        "1h": "60"
    }

    result = api_get(
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": symbol,
            "interval": interval_map[interval],
            "limit": 120
        }
    )

    candles = result["result"]["list"]

    # Bybit returns newest candles first
    candles = sorted(candles, key=lambda c: int(c[0]))

    # Ignore unfinished candle
    candles = candles[:-1]

    if len(candles) < 60:
        return None

    return {
        "close": [float(c[4]) for c in candles],
        "high": [float(c[2]) for c in candles],
        "low": [float(c[3]) for c in candles],
        "volume": [float(c[5]) for c in candles]
    }


# ---------- ANALYSIS ----------

def analyze(data):
    if not data:
        return None

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
        volume[-1] / avg_volume
        if avg_volume > 0 else 0
    )

    return {
        "price": close[-1],
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "rsi": current_rsi,
        "atr": atr,
        "volume_ratio": volume_ratio
    }


# ---------- SIGNAL GENERATOR ----------

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
        current_rsi = m15["rsi"]

        score = 65

        if direction == "LONG" and 50 <= current_rsi <= 70:
            score += 10

        elif direction == "SHORT" and 30 <= current_rsi <= 50:
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
            "rsi": current_rsi,
            "volume": m15["volume_ratio"]
        }

    except Exception as exc:
        print("Analysis error:", symbol, exc)
        return None


# ---------- SIGNAL MESSAGE ----------

def format_signal(signal):
    direction = (
        "🟢 LONG"
        if signal["direction"] == "LONG"
        else "🔴 SHORT"
    )

    targets = "\n".join(
        f"TP{i + 1}: {value:.8g}"
        for i, value in enumerate(signal["targets"])
    )

    return (
        "🚀 ARAD CRYPTO PRO V2\n\n"
        f"صرافی داده: Bybit Futures\n"
        f"ارز: {signal['symbol']}\n"
        f"جهت: {direction}\n"
        f"ورود: {signal['entry']:.8g}\n"
        f"حد ضرر: {signal['sl']:.8g}\n\n"
        f"{targets}\n\n"
        f"امتیاز الگوریتم: {signal['score']}\n"
        f"RSI 15m: {signal['rsi']:.1f}\n"
        f"نسبت حجم: {signal['volume']:.2f}x\n"
        "تأیید روند: 15m + 1h\n"
        f"زمان: {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
        "⚠️ امتیاز، احتمال موفقیت نیست. "
        "قبل از معامله بررسی شود."
    )


# ---------- SYMBOL DISCOVERY ----------

def get_symbols():
    active = set()
    cursor = None

    # Read all pages of USDT perpetual contracts
    while True:
        params = {
            "category": "linear",
            "limit": 1000
        }

        if cursor:
            params["cursor"] = cursor

        result = api_get(
            "/v5/market/instruments-info",
            params
        )

        for item in result["result"]["list"]:
            if (
                item.get("status") == "Trading"
                and item.get("quoteCoin") == "USDT"
                and item.get("contractType") == "LinearPerpetual"
            ):
                active.add(item["symbol"])

        cursor = result["result"].get("nextPageCursor")

        if not cursor:
            break

    # Meme coins that are actually listed
    memes = [
        symbol for symbol in MEME_COINS
        if symbol in active
    ]

    tickers_result = api_get(
        "/v5/market/tickers",
        {"category": "linear"}
    )

    tickers = [
        item for item in tickers_result["result"]["list"]
        if item["symbol"] in active
    ]

    tickers.sort(
        key=lambda x: float(x.get("turnover24h", 0)),
        reverse=True
    )

    top_symbols = [
        item["symbol"]
        for item in tickers[:MAX_COINS]
    ]

    return list(dict.fromkeys(memes + top_symbols))


# ---------- SIGNAL HISTORY ----------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def can_send(signal, state):
    key = signal["symbol"] + "_" + signal["direction"]
    last_sent = state.get(key, 0)

    return (
        time.time() - last_sent
        >= COOLDOWN_HOURS * 3600
    )


# ---------- MAIN ----------

def main():
    print("ARAD CRYPTO PRO V2 started.")
    print("UTC:", datetime.now(timezone.utc).isoformat())

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "BOT_TOKEN or CHAT_ID is missing."
        )

    state = load_state()

    try:
        symbols = get_symbols()

    except Exception as exc:
        error_message = (
            "⚠️ ARAD CRYPTO PRO V2\n"
            "دریافت اطلاعات بازار Bybit ناموفق بود.\n"
            f"خطا: {str(exc)[:300]}"
        )

        print(error_message)
        telegram_send(error_message)
        raise

    print("Symbols to scan:", len(symbols))

    sent_count = 0

    for index, symbol in enumerate(symbols, start=1):
        print(f"[{index}/{len(symbols)}] Scanning {symbol}")

        signal = make_signal(symbol)

        if signal is None:
            time.sleep(REQUEST_DELAY)
            continue

        if not can_send(signal, state):
            print("Cooldown active:", symbol)
            time.sleep(REQUEST_DELAY)
            continue

        if telegram_send(format_signal(signal)):
            key = signal["symbol"] + "_" + signal["direction"]
            state[key] = time.time()
            save_state(state)

            sent_count += 1
            print("Signal sent:", symbol)

        time.sleep(REQUEST_DELAY)

    print("Scan completed.")
    print("Signals sent:", sent_count)


if __name__ == "__main__":
    main()
