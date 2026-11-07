import os
import json
import time
import math
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# ==========================================================
# ARAD CRYPTO PRO V6.2 OURBIT TEST
# EARLY WATCH + CONFIRMED TELEGRAM ALERTS
# MTF | BOS | SWEEP | PULLBACK | RISK FILTERS
# ANALYSIS ONLY - NO ORDERS
# ==========================================================

BASE_URL = "https://futures.ourbit.io"
TELEGRAM_API = "https://api.telegram.org"

MAX_STAGE1_SYMBOLS = 10000  # scan the full eligible USDT futures universe
MAX_STAGE2_SYMBOLS = 80
MAX_STAGE3_SYMBOLS = 60

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

MIN_VOLUME_RATIO = 0.80
MIN_SCORE_WATCH = 5
MIN_SCORE_TRADE = 7
MIN_RR = 1.30
MAX_STOP_ATR = 2.00
MAX_LIVE_DIFF = 0.50
ATR_SL_MULTIPLIER = 1.20
PIVOT_LOOKBACK = 12
PULLBACK_MAX_ATR = 1.00

# Persistent anti-duplicate settings
WATCH_COOLDOWN_MIN = 45
CONFIRMED_COOLDOWN_MIN = 60
STATE_FILE = "arad_v62_test_signal_state.json"

EXCLUDED_BASES = {
    "USDC", "USDE", "DAI", "FDUSD", "TUSD", "USDP",
    "EUR", "GBP", "AUD", "JPY", "BRL", "TRY",
    "INDEX", "TEST",
}
METAL_KEYWORDS = ("GOLD", "XAU", "SILVER", "XAG")
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
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def http_json(url, method="GET", payload=None, headers=None, timeout=REQUEST_TIMEOUT):
    body = None
    request_headers = {"User-Agent": "ARAD-CRYPTO-PRO/6.2", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def api_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        data = http_json(url)
        if isinstance(data, dict):
            if data.get("success") is False:
                return None
            if data.get("code") not in (None, 0, "0"):
                return None
        return data
    except Exception as exc:
        log("API ERROR {}: {}".format(path, str(exc)[:160]))
        return None


def extract_data(payload):
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def get_contracts():
    data = extract_data(api_get("/api/v1/contract/detail"))
    if isinstance(data, dict):
        for key in ("contracts", "list", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


def get_tickers():
    data = extract_data(api_get("/api/v1/contract/ticker"))
    if isinstance(data, dict):
        for key in ("list", "rows", "tickers"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    result = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("symbol"):
                result[str(item["symbol"]).upper()] = item
    return result


def contract_is_enabled(contract):
    state = str(contract.get("state", contract.get("status", "0"))).upper()
    return state in ("0", "ENABLED", "ONLINE", "TRADING", "ACTIVE", "OPEN")


def is_target_symbol(symbol):
    symbol = str(symbol).upper()
    if not symbol.endswith("_USDT"):
        return False
    base = symbol[:-5]
    if base in EXCLUDED_BASES or len(base) < 2:
        return False
    return True


def ticker_turnover(ticker):
    return max(
        as_float(ticker.get("amount24")),
        as_float(ticker.get("turnover24")),
        as_float(ticker.get("quoteVolume")),
        as_float(ticker.get("volume24")),
    )


def ticker_price(ticker):
    return as_float(ticker.get("lastPrice", ticker.get("last", ticker.get("close"))))


def parse_candles(payload, interval_seconds):
    data = extract_data(payload)
    if not isinstance(data, dict):
        return []
    times = data.get("time") or data.get("timestamp") or []
    opens, highs, lows = data.get("open") or [], data.get("high") or [], data.get("low") or []
    closes = data.get("close") or []
    volumes = data.get("vol") or data.get("volume") or []
    candles = []

    if all(isinstance(x, list) for x in (times, opens, highs, lows, closes)):
        count = min(len(times), len(opens), len(highs), len(lows), len(closes))
        for i in range(count):
            ts = as_float(times[i])
            if ts > 10**12:
                ts /= 1000.0
            c = {
                "time": ts, "open": as_float(opens[i]), "high": as_float(highs[i]),
                "low": as_float(lows[i]), "close": as_float(closes[i]),
                "volume": as_float(volumes[i]) if i < len(volumes) else 0.0,
            }
            if ts > 0 and min(c["open"], c["high"], c["low"], c["close"]) > 0:
                candles.append(c)
    elif isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                ts = as_float(row.get("time", row.get("timestamp", row.get("t"))))
                c = {
                    "time": ts, "open": as_float(row.get("open", row.get("o"))),
                    "high": as_float(row.get("high", row.get("h"))),
                    "low": as_float(row.get("low", row.get("l"))),
                    "close": as_float(row.get("close", row.get("c"))),
                    "volume": as_float(row.get("vol", row.get("volume", row.get("v")))),
                }
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts = as_float(row[0])
                c = {
                    "time": ts, "open": as_float(row[1]), "high": as_float(row[2]),
                    "low": as_float(row[3]), "close": as_float(row[4]),
                    "volume": as_float(row[5]) if len(row) > 5 else 0.0,
                }
            else:
                continue
            if c["time"] > 10**12:
                c["time"] /= 1000.0
            if c["time"] > 0 and min(c["open"], c["high"], c["low"], c["close"]) > 0:
                candles.append(c)

    candles.sort(key=lambda x: x["time"])
    unique = {c["time"]: c for c in candles}
    now = time.time()
    return [c for c in unique.values() if c["time"] + interval_seconds <= now]


def get_candles(symbol, timeframe):
    interval, seconds = TIMEFRAMES[timeframe]
    end_time = int(time.time())
    start_time = end_time - seconds * CANDLE_LIMIT
    payload = api_get(
        "/api/v1/contract/kline/" + urllib.parse.quote(symbol, safe=""),
        {"interval": interval, "start": start_time, "end": end_time},
    )
    candles = parse_candles(payload, seconds)
    return candles if len(candles) >= MIN_CANDLES else []


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * multiplier + result * (1.0 - multiplier)
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        cur, prev = candles[i], candles[i - 1]["close"]
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev), abs(cur["low"] - prev)))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def volume_ratio(candles, period=20):
    if len(candles) < period + 1:
        return 0.0
    old = [c["volume"] for c in candles[-period - 1:-1]]
    average = sum(old) / len(old)
    return candles[-1]["volume"] / average if average > 0 else 0.0


def ema_series(values, period):
    if len(values) < period:
        return []
    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    result = [None] * (period - 1) + [current]
    for value in values[period:]:
        current = value * multiplier + current * (1.0 - multiplier)
        result.append(current)
    return result


def macd_histogram(values):
    if len(values) < 35:
        return 0.0
    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    macd_line = [
        (fast[i] - slow[i]) if fast[i] is not None and slow[i] is not None else None
        for i in range(len(values))
    ]
    valid = [x for x in macd_line if x is not None]
    if len(valid) < 9:
        return 0.0
    signal = ema_series(valid, 9)
    return valid[-1] - signal[-1] if signal and signal[-1] is not None else 0.0


def market_metrics(candles):
    closes = [c["close"] for c in candles]
    e9, e21, e50, e200 = (ema(closes, p) for p in (9, 21, 50, 200))
    if e200 is None:
        e200 = e50
    last = closes[-1]
    trend = "NEUTRAL"
    if e9 and e21 and e50 and last > e9 > e21 > e50 and last > e200:
        trend = "LONG"
    elif e9 and e21 and e50 and last < e9 < e21 < e50 and last < e200:
        trend = "SHORT"
    return {
        "price": last, "ema9": e9, "ema21": e21, "ema50": e50, "ema200": e200,
        "rsi": rsi(closes), "macd_hist": macd_histogram(closes), "atr": atr(candles),
        "volume_ratio": volume_ratio(candles), "trend": trend,
    }


def structure_signal(candles):
    if len(candles) < PIVOT_LOOKBACK + 5:
        return "NONE", False, False
    last, previous = candles[-1], candles[-2]
    window = candles[-PIVOT_LOOKBACK - 1:-1]
    resistance = max(c["high"] for c in window)
    support = min(c["low"] for c in window)
    bullish_bos = last["close"] > resistance and previous["close"] <= resistance
    bearish_bos = last["close"] < support and previous["close"] >= support
    recent = candles[-5:-1]
    recent_low = min(c["low"] for c in recent)
    recent_high = max(c["high"] for c in recent)
    bullish_sweep = last["low"] < recent_low and last["close"] > recent_low
    bearish_sweep = last["high"] > recent_high and last["close"] < recent_high
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
    price, e21, current_atr = metrics["price"], metrics["ema21"], metrics["atr"]
    if not e21 or current_atr <= 0:
        return False
    distance_atr = abs(price - e21) / current_atr
    if distance_atr > PULLBACK_MAX_ATR:
        return False
    # Require the pullback to hold on the correct side of EMA21.
    return price >= e21 if direction == "LONG" else price <= e21


def momentum_confirmed(m15, m5, direction):
    if direction == "LONG":
        return (
            m15["trend"] == "LONG" and m5["trend"] == "LONG"
            and m15["rsi"] >= 52 and m5["rsi"] >= 50
            and m15["macd_hist"] > 0 and m5["macd_hist"] > 0
        )
    return (
        m15["trend"] == "SHORT" and m5["trend"] == "SHORT"
        and m15["rsi"] <= 48 and m5["rsi"] <= 50
        and m15["macd_hist"] < 0 and m5["macd_hist"] < 0
    )


def room_to_structure(candidate, plan):
    candles = candidate.get("candles15", [])
    if len(candles) < 22:
        return False
    window = candles[-22:-2]
    if not window:
        return False
    entry, risk = plan["entry"], abs(plan["entry"] - plan["sl"])
    if risk <= 0:
        return False
    if candidate["direction"] == "LONG":
        resistance = max(c["high"] for c in window)
        return resistance <= entry or (resistance - entry) >= risk * 1.30
    support = min(c["low"] for c in window)
    return support >= entry or (entry - support) >= risk * 1.30


def analyze_higher_timeframes(c4, c1):
    m4, m1 = market_metrics(c4), market_metrics(c1)
    long_score = 3 * (m4["trend"] == "LONG") + 3 * (m1["trend"] == "LONG")
    short_score = 3 * (m4["trend"] == "SHORT") + 3 * (m1["trend"] == "SHORT")
    long_score += int(m4["rsi"] > 50) + int(m1["rsi"] > 50)
    short_score += int(m4["rsi"] < 50) + int(m1["rsi"] < 50)
    direction = "LONG" if long_score > short_score else "SHORT" if short_score > long_score else "NONE"
    return direction, long_score, short_score, m4, m1


def score_stage2(direction, long_score, short_score, candles):
    metrics = market_metrics(candles)
    structure, sweep, bos = structure_signal(candles)
    score = long_score if direction == "LONG" else short_score
    score += 2 if structure == direction else 0
    score += int(sweep) + int(metrics["trend"] == direction)
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
        targets = [entry + risk * x for x in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)]
    else:
        recent_high = max(c["high"] for c in recent_candles[-8:])
        stop = max(entry + risk_distance, recent_high + current_atr * 0.10)
        risk = stop - entry
        targets = [entry - risk * x for x in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)]
    if risk <= 0:
        return None
    stop_atr = risk / current_atr
    rr = abs(targets[-1] - entry) / risk
    if stop_atr > MAX_STOP_ATR or rr < MIN_RR:
        return None
    return {"entry": entry, "sl": stop, "tp": targets, "risk_atr": stop_atr, "rr": rr}


def analyze_symbol(symbol, ticker):
    price = ticker_price(ticker)
    if price <= 0:
        return None
    c4 = get_candles(symbol, "4h")
    time.sleep(REQUEST_PAUSE)
    c1 = get_candles(symbol, "1h")
    time.sleep(REQUEST_PAUSE)
    if not c4 or not c1:
        return None
    direction, ls, ss, m4, m1 = analyze_higher_timeframes(c4, c1)
    if direction == "NONE":
        return None
    score = max(ls, ss)
    if score < 4:
        return None
    return {
        "symbol": symbol, "direction": direction, "price": price,
        "long_score": ls, "short_score": ss, "score": score, "m4": m4, "m1": m1,
    }


def analyze_stage2(candidate):
    candles = get_candles(candidate["symbol"], "15m")
    time.sleep(REQUEST_PAUSE)
    if not candles:
        return None
    score, metrics, structure, sweep, bos = score_stage2(
        candidate["direction"], candidate["long_score"], candidate["short_score"], candles
    )
    if score < MIN_SCORE_WATCH:
        return None
    candidate.update({
        "score": score, "m15": metrics, "structure": structure, "sweep": sweep,
        "bos15": bos, "candles15": candles,
    })
    return candidate


def five_minute_trigger(candles, metrics, direction):
    if len(candles) < 3:
        return False
    last = candles[-1]
    previous = candles[-2]
    body = abs(last["close"] - last["open"])
    candle_range = max(last["high"] - last["low"], 1e-12)
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    if direction == "SHORT":
        rejection = last["close"] < last["open"] and last["close"] < metrics["ema9"] and upper_wick >= max(body * 0.35, candle_range * 0.10)
        continuation = last["close"] < last["open"] and last["close"] < previous["close"] and body / candle_range >= 0.35
        return rejection or continuation
    rejection = last["close"] > last["open"] and last["close"] > metrics["ema9"] and lower_wick >= max(body * 0.35, candle_range * 0.10)
    continuation = last["close"] > last["open"] and last["close"] > previous["close"] and body / candle_range >= 0.35
    return rejection or continuation


def candidate_quality(item):
    direction = item["direction"]
    score = item["score"]
    score += 2 if item.get("bos15") else 0
    score += 1 if item.get("sweep") else 0
    m15 = item.get("m15", {})
    if m15.get("volume_ratio", 0) >= 1.5:
        score += 2
    elif m15.get("volume_ratio", 0) >= MIN_VOLUME_RATIO:
        score += 1
    rsi15 = m15.get("rsi", 50)
    if direction == "SHORT" and 30 <= rsi15 <= 65:
        score += 1
    elif direction == "LONG" and 35 <= rsi15 <= 70:
        score += 1
    if item.get("m4", {}).get("trend") == direction and item.get("m1", {}).get("trend") == direction:
        score += 2
    return score


def analyze_stage3(candidate):
    direction = candidate["direction"]
    candles = get_candles(candidate["symbol"], "5m")
    time.sleep(REQUEST_PAUSE)
    if not candles:
        candidate["status"] = "WATCH"
        return candidate
    m5 = market_metrics(candles)
    structure5, sweep5, bos5 = structure_signal(candles)
    near_pullback = pullback_ok(m5, direction)
    trigger5 = five_minute_trigger(candles, m5, direction)
    score = candidate["score"]
    score += 1 if structure5 == direction else 0
    score += int(sweep5) + int(m5["trend"] == direction) + int(near_pullback) + int(trigger5)
    candidate.update({"m5": m5, "score": score, "bos5": bos5, "trigger5": trigger5})
    higher_aligned = candidate["m4"]["trend"] == direction and candidate["m1"]["trend"] == direction
    volume_ok = candidate["m15"]["volume_ratio"] >= MIN_VOLUME_RATIO or m5["volume_ratio"] >= MIN_VOLUME_RATIO
    live_diff = abs(candidate["price"] - m5["price"]) / m5["price"] * 100 if m5["price"] > 0 else 999
    live_ok = live_diff <= MAX_LIVE_DIFF
    momentum_ok = momentum_confirmed(candidate["m15"], m5, direction)
    structure_ok = candidate["bos15"] or candidate["sweep"]
    # A trend label alone cannot substitute for a fresh 5m trigger.
    trigger_ok = trigger5 or (bos5 and structure5 == direction)
    confirmed = (
        score >= MIN_SCORE_TRADE and higher_aligned and structure_ok
        and trigger_ok and near_pullback and momentum_ok
        and volume_ok and live_ok
    )
    if confirmed:
        plan = make_trade_plan(direction, m5["price"], m5["atr"], candles)
        if plan and room_to_structure(candidate, plan):
            candidate["status"] = "CONFIRMED"
            candidate["plan"] = plan
            return candidate
        candidate["rejection_reason"] = "RISK_OR_NEARBY_STRUCTURE"
    else:
        failed = []
        if not higher_aligned: failed.append("HTF")
        if not structure_ok: failed.append("15M_STRUCTURE")
        if not trigger_ok: failed.append("5M_TRIGGER")
        if not near_pullback: failed.append("PULLBACK")
        if not momentum_ok: failed.append("RSI_MACD_TREND")
        if not volume_ok: failed.append("VOLUME")
        if not live_ok: failed.append("LIVE_PRICE_GAP")
        candidate["rejection_reason"] = ",".join(failed) or "SCORE"
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


# ---------------- TELEGRAM ----------------

def telegram_send(message):
    token = os.getenv("BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()
    if not token or not chat_id:
        log("TELEGRAM SKIP: BOT_TOKEN or CHAT_ID secret is missing")
        return False
    url = "{}/bot{}/sendMessage".format(TELEGRAM_API, token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        result = http_json(url, method="POST", payload=payload, timeout=20)
        if isinstance(result, dict) and result.get("ok") is True:
            log("TELEGRAM SENT")
            return True
        log("TELEGRAM ERROR: {}".format(str(result)[:220]))
    except Exception as exc:
        log("TELEGRAM ERROR: {}".format(str(exc)[:180]))
    return False


# Store deduplication state in the repository using the Actions GITHUB_TOKEN.
# The workflow must grant contents: write and pass GITHUB_TOKEN to this script.
def github_state_request(method, payload=None, sha=None):
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    branch = os.getenv("GITHUB_REF_NAME", "main").strip()
    if not token or not repo:
        return None
    url = "https://api.github.com/repos/{}/contents/{}".format(
        repo, urllib.parse.quote(STATE_FILE)
    )
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if method == "GET":
        try:
            data = http_json(url + "?ref=" + urllib.parse.quote(branch), headers=headers)
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            return {"state": json.loads(content), "sha": data.get("sha")}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"state": {}, "sha": None}
            log("STATE READ ERROR: HTTP {}".format(exc.code))
            return None
        except Exception as exc:
            log("STATE READ ERROR: {}".format(str(exc)[:150]))
            return None
    if method == "PUT":
        content = base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
        body = {
            "message": "Update ARAD signal deduplication state",
            "content": content,
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        try:
            result = http_json(url, method="PUT", payload=body, headers=headers)
            return bool(isinstance(result, dict) and result.get("content"))
        except Exception as exc:
            log("STATE SAVE ERROR: {}".format(str(exc)[:180]))
            return False
    return None


def load_signal_state():
    result = github_state_request("GET")
    if result is None:
        log("WARNING: persistent anti-duplicate state unavailable")
        return {}, None, False
    return result["state"], result.get("sha"), True


def save_signal_state(state, sha):
    saved = github_state_request("PUT", payload=state, sha=sha)
    if not saved:
        log("WARNING: signal state was not saved; duplicate alerts may occur next run")
    return saved


def should_send_alert(state, item, alert_type):
    symbol = item["symbol"]
    direction = item["direction"]
    key = symbol + "|" + direction
    now = int(time.time())
    previous = state.get(key, {})
    cooldown = WATCH_COOLDOWN_MIN if alert_type == "WATCH" else CONFIRMED_COOLDOWN_MIN

    # A confirmed alert is allowed immediately after a WATCH alert.
    if alert_type == "CONFIRMED" and previous.get("type") == "WATCH":
        return True
    if previous.get("type") == alert_type and now - int(previous.get("sent_at", 0)) < cooldown * 60:
        return False
    return True


def mark_alert_sent(state, item, alert_type):
    key = item["symbol"] + "|" + item["direction"]
    state[key] = {"type": alert_type, "sent_at": int(time.time())}
    # Keep the state file small.
    cutoff = int(time.time()) - 7 * 24 * 60 * 60
    return {k: v for k, v in state.items() if int(v.get("sent_at", 0)) >= cutoff}


def watch_message(item):
    m15 = item["m15"]
    lines = [
        "🟡 ARAD CRYPTO PRO | WATCH",
        "هشدار زودهنگام - هنوز نقطه ورود قطعی نیست",
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت احتمالی: {}".format("خرید LONG" if item["direction"] == "LONG" else "فروش SHORT"),
        "قیمت فعلی: {}".format(format_price(item["price"])),
        "امتیاز تحلیلی: {} امتیاز".format(item["score"]),
        "4H: {} | RSI {:.1f}".format(item["m4"]["trend"], item["m4"]["rsi"]),
        "1H: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]),
        "15M: {} | RSI {:.1f} | حجم {:.2f}x".format(m15["trend"], m15["rsi"], m15["volume_ratio"]),
        "BOS 15m: {}".format("بله" if item["bos15"] else "خیر"),
        "Sweep: {}".format("بله" if item["sweep"] else "خیر"),
        "",
        "⚠️ فقط تحت نظر بگیر؛ ورود بدون تأیید نهایی توصیه نمی‌شود.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def confirmed_message(item):
    plan = item["plan"]
    lines = [
        "🟢 ARAD CRYPTO PRO | CONFIRMED",
        "سیگنال تأییدشده - بررسی ورود با مدیریت ریسک",
        "",
        "نماد: {}".format(item["symbol"]),
        "جهت: {}".format("LONG خرید" if item["direction"] == "LONG" else "SHORT فروش"),
        "قیمت لحظه تحلیل: {}".format(format_price(item["price"])),
        "ورود پیشنهادی: {}".format(format_price(plan["entry"])),
        "حد ضرر: {}".format(format_price(plan["sl"])),
    ]
    for i, target in enumerate(plan["tp"], 1):
        lines.append("TP{}: {}".format(i, format_price(target)))
    lines += [
        "فاصله حد ضرر: {:.2f} ATR".format(plan["risk_atr"]),
        "نسبت سود به زیان تا TP6: {:.2f}".format(plan["rr"]),
        "امتیاز تحلیلی: {} امتیاز".format(item["score"]),
        "",
        "⚠️ این پیام تحلیل است، نه دستور معامله. سفارش خودکار ثبت نشده است.",
        "زمان: " + now_text(),
    ]
    return "\n".join(lines)


def dispatch_alerts(watch_items, confirmed_items):
    state, sha, state_ok = load_signal_state()
    if not state_ok:
        # Fail closed: avoid flooding Telegram if deduplication cannot be checked.
        log("ALERTS PAUSED: cannot verify duplicate-alert state")
        return

    sent_any = False
    for item in watch_items:
        if should_send_alert(state, item, "WATCH"):
            if telegram_send(watch_message(item)):
                state = mark_alert_sent(state, item, "WATCH")
                sent_any = True
        else:
            log("WATCH DUPLICATE SKIPPED: {}".format(item["symbol"]))

    for item in confirmed_items:
        if should_send_alert(state, item, "CONFIRMED"):
            if telegram_send(confirmed_message(item)):
                state = mark_alert_sent(state, item, "CONFIRMED")
                sent_any = True
        else:
            log("CONFIRMED DUPLICATE SKIPPED: {}".format(item["symbol"]))

    if sent_any:
        save_signal_state(state, sha)
    else:
        log("TELEGRAM: no new alerts to send")


def print_result(item):
    log("")
    log("=" * 58)
    log("{} | {} | SCORE {} POINTS".format(item["symbol"], item["direction"], item["score"]))
    log("STATUS: {}".format(item.get("status", "WATCH")))
    log("PRICE: {}".format(format_price(item["price"])))
    log("4H: {} | RSI {:.1f}".format(item["m4"]["trend"], item["m4"]["rsi"]))
    log("1H: {} | RSI {:.1f}".format(item["m1"]["trend"], item["m1"]["rsi"]))
    log("15M: {} | RSI {:.1f} | VOL {:.2f}x".format(
        item["m15"]["trend"], item["m15"]["rsi"], item["m15"]["volume_ratio"]
    ))
    if "m5" in item:
        log("5M: {} | RSI {:.1f} | VOL {:.2f}x".format(
            item["m5"]["trend"], item["m5"]["rsi"], item["m5"]["volume_ratio"]
        ))
    log("15M BOS: {} | SWEEP: {}".format(item.get("bos15", False), item.get("sweep", False)))
    if "m5" in item:
        log("5M TRIGGER: {} | MACD HIST 15m/5m: {:.8f}/{:.8f}".format(
            item.get("trigger5", False), item["m15"].get("macd_hist", 0.0),
            item["m5"].get("macd_hist", 0.0)
        ))
    if item.get("rejection_reason"):
        log("CONFIRMATION BLOCK: {}".format(item["rejection_reason"]))
    if item.get("status") == "CONFIRMED" and "plan" in item:
        plan = item["plan"]
        log("ENTRY: {}".format(format_price(plan["entry"])))
        log("STOP LOSS: {}".format(format_price(plan["sl"])))
        for i, target in enumerate(plan["tp"], 1):
            log("TP{}: {}".format(i, format_price(target)))
        log("STOP DISTANCE: {:.2f} ATR | R/R: {:.2f}".format(plan["risk_atr"], plan["rr"]))
    else:
        log("WATCH ONLY - WAIT FOR CONFIRMATION")
    log("=" * 58)


def main():
    started = time.time()
    log("")
    log("ARAD CRYPTO PRO V6.2 OURBIT TEST")
    log("START: " + now_text())
    log("MODE: FULL ELIGIBLE UNIVERSE + QUALITY RANKING + PULLBACK CONFIRMATION | NO ORDERS")
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
        if not symbol or not is_target_symbol(symbol) or not contract_is_enabled(contract):
            continue
        ticker = tickers.get(symbol, {})
        price = ticker_price(ticker)
        if price <= 0:
            continue
        universe.append({"symbol": symbol, "ticker": ticker, "turnover": ticker_turnover(ticker)})

    universe.sort(key=lambda x: x["turnover"], reverse=True)
    pinned = [x for x in universe if any(word in x["symbol"] for word in METAL_KEYWORDS)]
    selected = universe[:MAX_STAGE1_SYMBOLS]
    selected_symbols = {x["symbol"] for x in selected}
    for item in pinned:
        if item["symbol"] not in selected_symbols:
            selected.append(item)
            selected_symbols.add(item["symbol"])

    log("ELIGIBLE CONTRACTS: {}".format(len(universe)))
    log("STAGE 1 SCAN COUNT: {}".format(len(selected)))
    log("METAL CONTRACTS PINNED: {}".format(len(pinned)))

    stage1 = []
    for i, item in enumerate(selected, 1):
        if PRINT_ALL_STAGES:
            log("STAGE 1: {} / {} {}".format(i, len(selected), item["symbol"]))
        result = analyze_symbol(item["symbol"], item["ticker"])
        if result:
            stage1.append(result)
            log("PROMISING: {} {} SCORE {}".format(result["symbol"], result["direction"], result["score"]))

    stage1.sort(key=lambda x: x["score"], reverse=True)
    stage1 = stage1[:MAX_STAGE2_SYMBOLS]
    log("STAGE 1 COMPLETE: {}".format(len(stage1)))

    stage2 = []
    for i, candidate in enumerate(stage1, 1):
        log("STAGE 2: {} / {} {}".format(i, len(stage1), candidate["symbol"]))
        result = analyze_stage2(candidate)
        if result:
            stage2.append(result)
            log("STAGE 2 PASS: {} {} SCORE {}".format(
                result["symbol"], result["direction"], result["score"]
            ))

    stage2.sort(key=candidate_quality, reverse=True)
    stage2 = stage2[:MAX_STAGE3_SYMBOLS]
    log("STAGE 2 COMPLETE: {}".format(len(stage2)))

    # Send early WATCH alerts as soon as 15m analysis passes.
    # A later CONFIRMED message upgrades the WATCH if 5m checks pass.
    log("WATCH notifications disabled in V6 test; only confirmed entries can alert.")

    final_results = []
    for i, candidate in enumerate(stage2, 1):
        log("STAGE 3: {} / {} {}".format(i, len(stage2), candidate["symbol"]))
        result = analyze_stage3(candidate)
        if result:
            final_results.append(result)
            print_result(result)

    confirmed = [x for x in final_results if x.get("status") == "CONFIRMED"]
    watch = [x for x in final_results if x.get("status") == "WATCH"]

    # Send only confirmed here; WATCH was already sent at stage 2.
    dispatch_alerts([], confirmed)

    elapsed = time.time() - started
    log("")
    log("=" * 58)
    log("SCAN FINISHED")
    log("TIME: " + now_text())
    log("ELIGIBLE CONTRACTS SCANNED: {}".format(len(selected)))
    log("STAGE 1 PROMISING: {}".format(len(stage1)))
    log("STAGE 2 PASSED / WATCH CANDIDATES: {}".format(len(stage2)))
    log("CONFIRMED SETUPS: {}".format(len(confirmed)))
    log("WATCH SETUPS: {}".format(len(watch)))
    log("ELAPSED: {:.1f} seconds".format(elapsed))
    log("NO ORDERS WERE PLACED")
    log("=" * 58)


if __name__ == "__main__":
    main()
