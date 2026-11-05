
import json
import time
import math
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V5.5 OURBIT
# EARLY DETECTION | MTF | BOS | SWEEP | PULLBACK
# CRYPTO + MEME + GOLD + SILVER
# ANALYSIS ONLY - NO ORDERS - NO TELEGRAM
# ==========================================================

BASE_URL = "https://futures.ourbit.io"

# ---------- SCAN SETTINGS ----------
MAX_STAGE1_SYMBOLS = 120
MAX_STAGE2_SYMBOLS = 35
MAX_STAGE3_SYMBOLS = 18

TIMEFRAMES = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "1h": ("Min60", 3600),
    "4h": ("Hour4", 14400),
}

CANDLE_LIMIT = 220
MIN_CANDLES = 80
REQUEST_TIMEOUT = 18
REQUEST_PAUSE = 0.12

# ---------- QUALITY / RISK FILTERS ----------
MIN_VOLUME_RATIO = 0.80
MIN_SCORE_WATCH = 5
MIN_SCORE_TRADE = 7
MIN_RR = 1.30
MAX_STOP_ATR = 2.00
MAX_LIVE_DIFF = 0.50
ATR_SL_MULTIPLIER = 1.20
PIVOT_LOOKBACK = 12
PULLBACK_MAX_ATR = 1.50

# ---------- SYMBOL FILTERS ----------
EXCLUDED_BASES = {
    "USDC", "USDE", "DAI", "FDUSD", "TUSD", "USDP",
    "EUR", "GBP", "AUD", "JPY", "BRL", "TRY",
    "INDEX", "TEST",
}

METAL_KEYWORDS = ("GOLD", "XAU", "SILVER", "XAG")

# ---------- OUTPUT ----------
PRINT_ALL_STAGES = True


def log(message):
    print(message, flush=True)


def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def as_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        result = float(value)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def api_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ARAD-CRYPTO-PRO/5.5",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw)

        if isinstance(data, dict):
            if data.get("success") is False:
                return None
            if data.get("code") not in (None, 0, "0"):
                return None

        return data

    except Exception as exc:
        log("API ERROR {}: {}".format(path, str(exc)[:180]))
        return None


def extract_data(payload):
    if not isinstance(payload, dict):
        return payload
    return payload.get("data", payload)


def get_contracts():
    payload = api_get("/api/v1/contract/detail")
    data = extract_data(payload)

    if isinstance(data, dict):
        for key in ("contracts", "list", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]

    if isinstance(data, list):
        return data

    return []


def get_tickers():
    payload = api_get("/api/v1/contract/ticker")
    data = extract_data(payload)

    if isinstance(data, dict):
        for key in ("list", "rows", "tickers"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    result = {}
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if symbol:
                result[str(symbol).upper()] = item

    return result


def contract_is_enabled(contract):
    state = contract.get("state", contract.get("status", "0"))
    state = str(state).upper()

    # Ourbit contract state 0 is accepted as active by this scanner.
    return state in ("0", "ENABLED", "ONLINE", "TRADING", "ACTIVE", "OPEN")


def is_target_symbol(symbol):
    symbol = str(symbol).upper()

    if not symbol.endswith("_USDT"):
        return False

    base = symbol[:-5]

    if base in EXCLUDED_BASES:
        return False

    if any(word in base for word in METAL_KEYWORDS):
        return True

    # Exclude obvious non-crypto symbols, but retain numeric meme contracts.
    if len(base) < 2:
        return False

    return True


def ticker_turnover(ticker):
    if not isinstance(ticker, dict):
        return 0.0

    return max(
        as_float(ticker.get("amount24")),
        as_float(ticker.get("turnover24")),
        as_float(ticker.get("quoteVolume")),
        as_float(ticker.get("volume24")),
    )


def ticker_price(ticker):
    if not isinstance(ticker, dict):
        return 0.0
    return as_float(
        ticker.get("lastPrice", ticker.get("last", ticker.get("close")))
    )


def parse_candles(payload, interval_seconds):
    data = extract_data(payload)
    if not isinstance(data, dict):
        return []

    times = data.get("time") or data.get("timestamp") or []
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    volumes = data.get("vol") or data.get("volume") or []

    candles = []

    # Ourbit array response format.
    if all(isinstance(x, list) for x in (times, opens, highs, lows, closes)):
        count = min(
            len(times), len(opens), len(highs),
            len(lows), len(closes)
        )

        for i in range(count):
            timestamp = as_float(times[i])
            if timestamp <= 0:
                continue

            if timestamp > 10**12:
                timestamp /= 1000.0

            candle = {
                "time": timestamp,
                "open": as_float(opens[i]),
                "high": as_float(highs[i]),
                "low": as_float(lows[i]),
                "close": as_float(closes[i]),
                "volume": as_float(volumes[i]) if i < len(volumes) else 0.0,
            }

            if (
                candle["open"] > 0
                and candle["high"] > 0
                and candle["low"] > 0
                and candle["close"] > 0
            ):
                candles.append(candle)

    # Alternative list-of-candles response format.
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                timestamp = as_float(
                    row.get("time", row.get("timestamp", row.get("t")))
                )
                candle = {
                    "time": timestamp,
                    "open": as_float(row.get("open", row.get("o"))),
                    "high": as_float(row.get("high", row.get("h"))),
                    "low": as_float(row.get("low", row.get("l"))),
                    "close": as_float(row.get("close", row.get("c"))),
                    "volume": as_float(
                        row.get("vol", row.get("volume", row.get("v")))
                    ),
                }
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                timestamp = as_float(row[0])
                candle = {
                    "time": timestamp,
                    "open": as_float(row[1]),
                    "high": as_float(row[2]),
                    "low": as_float(row[3]),
                    "close": as_float(row[4]),
                    "volume": as_float(row[5]) if len(row) > 5 else 0.0,
                }
            else:
                continue

            if candle["time"] > 10**12:
                candle["time"] /= 1000.0

            if (
                candle["time"] > 0
                and candle["open"] > 0
                and candle["high"] > 0
                and candle["low"] > 0
                and candle["close"] > 0
            ):
                candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    # Remove duplicate timestamps.
    unique = {}
    for candle in candles:
        unique[candle["time"]] = candle

    candles = list(unique.values())

    # Exclude the currently forming candle.
    current_time = time.time()
    candles = [
        c for c in candles
        if c["time"] + interval_seconds <= current_time
    ]

    return candles


def get_candles(symbol, timeframe):
    interval, seconds = TIMEFRAMES[timeframe]

    end_time = int(time.time())
    start_time = end_time - seconds * CANDLE_LIMIT

    payload = api_get(
        "/api/v1/contract/kline/" + urllib.parse.quote(symbol, safe=""),
        {
            "interval": interval,
            "start": start_time,
            "end": end_time,
        },
    )

    candles = parse_candles(payload, seconds)

    if len(candles) < MIN_CANDLES:
        return []

    return candles


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (value * multiplier) + (result * (1.0 - multiplier))

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    true_ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous_close = candles[i - 1]["close"]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous_close),
            abs(current["low"] - previous_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return 0.0

    value = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        value = ((value * (period - 1)) + tr) / period

    return value


def volume_ratio(candles, period=20):
    if len(candles) < period + 1:
        return 0.0

    previous = [c["volume"] for c in candles[-period - 1:-1]]
    average = sum(previous) / len(previous)

    if average <= 0:
        return 0.0

    return candles[-1]["volume"] / average


def market_metrics(candles):
    closes = [c["close"] for c in candles]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    if e200 is None:
        e200 = e50

    last = closes[-1]
    current_rsi = rsi(closes, 14)
    current_atr = atr(candles, 14)
    current_volume_ratio = volume_ratio(candles, 20)

    trend = "NEUTRAL"

    if e9 and e21 and e50 and last > e9 > e21 > e50 and last > e200:
        trend = "LONG"
    elif e9 and e21 and e50 and last < e9 < e21 < e50 and last < e200:
        trend = "SHORT"

    return {
        "price": last,
        "ema9": e9,
        "ema21": e21,
        "ema50": e50,
        "ema200": e200,
        "rsi": current_rsi,
        "atr": current_atr,
        "volume_ratio": current_volume_ratio,
        "trend": trend,
    }


def structure_signal(candles):
    if len(candles) < PIVOT_LOOKBACK + 5:
        return "NONE", False, False

    last = candles[-1]
    previous = candles[-2]
    window = candles[-PIVOT_LOOKBACK - 1:-1]

    resistance = max(c["high"] for c in window)
    support = min(c["low"] for c in window)

    bullish_bos = (
        last["close"] > resistance
        and previous["close"] <= resistance
    )
    bearish_bos = (
        last["close"] < support
        and previous["close"] >= support
    )

    recent = candles[-5:-1]
    recent_low = min(c["low"] for c in recent)
    recent_high = max(c["high"] for c in recent)

    bullish_sweep = (
        last["low"] < recent_low
        and last["close"] > recent_low
    )
    bearish_sweep = (
        last["high"] > recent_high
        and last["close"] < recent_high
    )

    if bullish_bos:
        return "LONG", bullish_sweep, True
    if bearish_bos:
        return "SHORT", bearish_sweep, True
    if bullish_sweep:
        return "LONG", True, False
    if bearish_sweep:
        return "SHORT", True, False

    return "NONE", False, False


def pullback_ok(metrics, direction):
    price = metrics["price"]
    e21 = metrics["ema21"]
    current_atr = metrics["atr"]

    if not e21 or current_atr <= 0:
        return False

    distance = abs(price - e21) / current_atr

    if distance > PULLBACK_MAX_ATR:
        return False

    if direction == "LONG":
        return price >= e21 * 0.997

    return price <= e21 * 1.003


def analyze_higher_timeframes(candles_4h, candles_1h):
    m4 = market_metrics(candles_4h)
    m1 = market_metrics(candles_1h)

    long_score = 0
    short_score = 0

    if m4["trend"] == "LONG":
        long_score += 3
    elif m4["trend"] == "SHORT":
        short_score += 3

    if m1["trend"] == "LONG":
        long_score += 3
    elif m1["trend"] == "SHORT":
        short_score += 3

    if m4["rsi"] > 50:
        long_score += 1
    elif m4["rsi"] < 50:
        short_score += 1

    if m1["rsi"] > 50:
        long_score += 1
    elif m1["rsi"] < 50:
        short_score += 1

    direction = "NONE"
    if long_score > short_score:
        direction = "LONG"
    elif short_score > long_score:
        direction = "SHORT"

    return direction, long_score, short_score, m4, m1


def score_stage2(direction, long_score, short_score, candles_15m):
    metrics = market_metrics(candles_15m)
    structure, sweep, bos = structure_signal(candles_15m)

    score = long_score if direction == "LONG" else short_score

    if structure == direction:
        score += 2
    if sweep:
        score += 1
    if metrics["trend"] == direction:
        score += 1

    if direction == "LONG" and metrics["rsi"] >= 48:
        score += 1
    elif direction == "SHORT" and metrics["rsi"] <= 52:
        score += 1

    return score, metrics, structure, sweep, bos


def make_trade_plan(direction, entry, current_atr, recent_candles):
    if entry <= 0 or current_atr <= 0:
        return None

    risk_distance = current_atr * ATR_SL_MULTIPLIER

    if direction == "LONG":
        recent_low = min(c["low"] for c in recent_candles[-8:])
        stop = min(entry - risk_distance, recent_low - current_atr * 0.10)
        risk = entry - stop

        if risk <= 0:
            return None

        targets = [
            entry + risk * 1.0,
            entry + risk * 1.5,
            entry + risk * 2.0,
            entry + risk * 2.5,
        ]

    else:
        recent_high = max(c["high"] for c in recent_candles[-8:])
        stop = max(entry + risk_distance, recent_high + current_atr * 0.10)
        risk = stop - entry

        if risk <= 0:
            return None

        targets = [
            entry - risk * 1.0,
            entry - risk * 1.5,
            entry - risk * 2.0,
            entry - risk * 2.5,
        ]

    stop_atr = risk / current_atr
    rr = abs(targets[-1] - entry) / risk

    if stop_atr > MAX_STOP_ATR or rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": stop,
        "tp": targets,
        "risk_atr": stop_atr,
        "rr": rr,
    }


def analyze_symbol(symbol, ticker):
    price = ticker_price(ticker)
    if price <= 0:
        return None

    candles_4h = get_candles(symbol, "4h")
    time.sleep(REQUEST_PAUSE)
    candles_1h = get_candles(symbol, "1h")
    time.sleep(REQUEST_PAUSE)

    if not candles_4h or not candles_1h:
        return None

    direction, long_score, short_score, m4, m1 = (
        analyze_higher_timeframes(candles_4h, candles_1h)
    )

    if direction == "NONE":
        return None

    higher_score = max(long_score, short_score)

    if higher_score < 4:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "price": price,
        "long_score": long_score,
        "short_score": short_score,
        "score": higher_score,
        "m4": m4,
        "m1": m1,
    }


def analyze_stage2(candidate):
    symbol = candidate["symbol"]

    candles_15m = get_candles(symbol, "15m")
    time.sleep(REQUEST_PAUSE)

    if not candles_15m:
        return None

    score, metrics, structure, sweep, bos = score_stage2(
        candidate["direction"],
        candidate["long_score"],
        candidate["short_score"],
        candles_15m,
    )

    if score < MIN_SCORE_WATCH:
        return None

    candidate.update({
        "score": score,
        "m15": metrics,
        "structure": structure,
        "sweep": sweep,
        "bos15": bos,
        "candles15": candles_15m,
    })

    return candidate


def analyze_stage3(candidate):
    symbol = candidate["symbol"]
    direction = candidate["direction"]

    candles_5m = get_candles(symbol, "5m")
    time.sleep(REQUEST_PAUSE)

    if not candles_5m:
        candidate["status"] = "WATCH"
        return candidate

    m5 = market_metrics(candles_5m)
    structure5, sweep5, bos5 = structure_signal(candles_5m)

    score = candidate["score"]

    if structure5 == direction:
        score += 1
    if sweep5:
        score += 1
    if m5["trend"] == direction:
        score += 1
    if pullback_ok(m5, direction):
        score += 1

    candidate["m5"] = m5
    candidate["score"] = score
    candidate["bos5"] = bos5

    # Confirmed setup requires higher timeframe agreement,
    # a 15m BOS, 5m confirmation, volume and pullback quality.
    higher_aligned = (
        candidate["m4"]["trend"] == direction
        and candidate["m1"]["trend"] == direction
    )

    volume_ok = (
        candidate["m15"]["volume_ratio"] >= MIN_VOLUME_RATIO
        or m5["volume_ratio"] >= MIN_VOLUME_RATIO
    )

    live_diff = abs(candidate["price"] - m5["price"]) / m5["price"] * 100
    live_ok = live_diff <= MAX_LIVE_DIFF

    confirmed = (
        score >= MIN_SCORE_TRADE
        and higher_aligned
        and candidate["bos15"]
        and (bos5 or structure5 == direction)
        and pullback_ok(m5, direction)
        and volume_ok
        and live_ok
    )

    if confirmed:
        plan = make_trade_plan(
            direction,
            m5["price"],
            m5["atr"],
            candles_5m,
        )

        if plan:
            candidate["status"] = "CONFIRMED"
            candidate["plan"] = plan
            return candidate

    candidate["status"] = "WATCH"
    candidate["live_diff"] = live_diff
    return candidate


def format_price(value):
    if value >= 1000:
        return "{:,.4f}".format(value)
    if value >= 1:
        return "{:.5f}".format(value)
    if value >= 0.01:
        return "{:.7f}".format(value)
    return "{:.10f}".format(value)


def print_result(item):
    direction = item["direction"]
    status = item.get("status", "WATCH")

    log("")
    log("=" * 58)
    log("{} | {} | SCORE {}/10".format(
        item["symbol"], direction, item["score"]
    ))
    log("STATUS: {}".format(status))
    log("PRICE: {}".format(format_price(item["price"])))
    log("4H: {} | RSI {:.1f}".format(
        item["m4"]["trend"], item["m4"]["rsi"]
    ))
    log("1H: {} | RSI {:.1f}".format(
        item["m1"]["trend"], item["m1"]["rsi"]
    ))
    log("15M: {} | RSI {:.1f} | VOL {:.2f}x".format(
        item["m15"]["trend"],
        item["m15"]["rsi"],
        item["m15"]["volume_ratio"],
    ))

    if "m5" in item:
        log("5M: {} | RSI {:.1f} | VOL {:.2f}x".format(
            item["m5"]["trend"],
            item["m5"]["rsi"],
            item["m5"]["volume_ratio"],
        ))

    log("15M BOS: {} | SWEEP: {}".format(
        item.get("bos15", False),
        item.get("sweep", False),
    ))

    if status == "CONFIRMED" and "plan" in item:
        plan = item["plan"]
        log("ENTRY: {}".format(format_price(plan["entry"])))
        log("STOP LOSS: {}".format(format_price(plan["sl"])))

        for index, target in enumerate(plan["tp"], 1):
            log("TP{}: {}".format(index, format_price(target)))

        log("STOP DISTANCE: {:.2f} ATR".format(plan["risk_atr"]))
        log("R/R: {:.2f}".format(plan["rr"]))
    else:
        log("WATCH ONLY - WAIT FOR CONFIRMATION")

    log("=" * 58)


def main():
    started = time.time()

    log("")
    log("ARAD CRYPTO PRO V5.5 OURBIT")
    log("START: " + now_text())
    log("MODE: ANALYSIS ONLY - NO ORDERS - NO TELEGRAM")
    log("BASE URL: " + BASE_URL)

    ping = api_get("/api/v1/contract/ping")
    if ping is None:
        log("PING: FAILED")
        return

    log("PING: OK")

    contracts = get_contracts()
    if not contracts:
        log("ERROR: CONTRACT DETAIL EMPTY")
        return

    tickers = get_tickers()
    log("CONTRACTS RECEIVED: {}".format(len(contracts)))
    log("TICKERS RECEIVED: {}".format(len(tickers)))

    universe = []
    for contract in contracts:
        if not isinstance(contract, dict):
            continue

        symbol = str(contract.get("symbol", "")).upper()
        if not symbol or not is_target_symbol(symbol):
            continue
        if not contract_is_enabled(contract):
            continue

        ticker = tickers.get(symbol, {})
        price = ticker_price(ticker)
        turnover = ticker_turnover(ticker)

        if price <= 0:
            continue

        universe.append({
            "symbol": symbol,
            "ticker": ticker,
            "turnover": turnover,
        })

    # Rank by 24h turnover, but keep gold/silver contracts in the scan.
    universe.sort(key=lambda x: x["turnover"], reverse=True)

    pinned_metals = [
        item for item in universe
        if any(word in item["symbol"] for word in METAL_KEYWORDS)
    ]

    selected = universe[:MAX_STAGE1_SYMBOLS]
    selected_symbols = {x["symbol"] for x in selected}

    for item in pinned_metals:
        if item["symbol"] not in selected_symbols:
            selected.append(item)
            selected_symbols.add(item["symbol"])

    log("ELIGIBLE CONTRACTS: {}".format(len(universe)))
    log("STAGE 1 SCAN COUNT: {}".format(len(selected)))
    log("METAL CONTRACTS PINNED: {}".format(len(pinned_metals)))

    stage1 = []

    for index, item in enumerate(selected, 1):
        symbol = item["symbol"]

        if PRINT_ALL_STAGES:
            log("STAGE 1: {} / {} {}".format(
                index, len(selected), symbol
            ))

        result = analyze_symbol(symbol, item["ticker"])

        if result:
            stage1.append(result)
            log("PROMISING: {} {} SCORE {}".format(
                symbol, result["direction"], result["score"]
            ))

    stage1.sort(key=lambda x: x["score"], reverse=True)
    stage1 = stage1[:MAX_STAGE2_SYMBOLS]

    log("")
    log("STAGE 1 COMPLETE: {}".format(len(stage1)))

    stage2 = []

    for index, candidate in enumerate(stage1, 1):
        log("STAGE 2: {} / {} {}".format(
            index, len(stage1), candidate["symbol"]
        ))

        result = analyze_stage2(candidate)
        if result:
            stage2.append(result)
            log("STAGE 2 PASS: {} {} SCORE {}".format(
                result["symbol"],
                result["direction"],
                result["score"],
            ))

    stage2.sort(key=lambda x: x["score"], reverse=True)
    stage2 = stage2[:MAX_STAGE3_SYMBOLS]

    log("")
    log("STAGE 2 COMPLETE: {}".format(len(stage2)))

    final_results = []

    for index, candidate in enumerate(stage2, 1):
        log("STAGE 3: {} / {} {}".format(
            index, len(stage2), candidate["symbol"]
        ))

        result = analyze_stage3(candidate)
        if result:
            final_results.append(result)
            print_result(result)

    confirmed = [
        x for x in final_results
        if x.get("status") == "CONFIRMED"
    ]
    watch = [
        x for x in final_results
        if x.get("status") == "WATCH"
    ]

    elapsed = time.time() - started

    log("")
    log("=" * 58)
    log("SCAN FINISHED")
    log("TIME: " + now_text())
    log("STAGE 1 PROMISING: {}".format(len(stage1)))
    log("STAGE 2 PASSED: {}".format(len(stage2)))
    log("CONFIRMED SETUPS: {}".format(len(confirmed)))
    log("WATCH SETUPS: {}".format(len(watch)))
    log("ELAPSED: {:.1f} seconds".format(elapsed))
    log("NO ORDERS WERE PLACED")
    log("NO TELEGRAM MESSAGES WERE SENT")
    log("=" * 58)


if __name__ == "__main__":
    main()
