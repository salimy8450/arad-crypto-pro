import os, json, time, base64, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone

BASE_URL = "https://futures.ourbit.io"
TELEGRAM_API = "https://api.telegram.org"
OUTCOME_FILE = "arad_signal_outcomes.json"
TIMEFRAME_SECONDS = 300
MAX_AGE_SECONDS = 24 * 60 * 60
TIMEOUT = 20

def log(s):
    print(s, flush=True)

def http_json(url, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    h = {"User-Agent": "ARAD-OUTCOME-TRACKER/1.0", "Accept": "application/json"}
    if headers: h.update(headers)
    if payload is not None: h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8", errors="replace"))

def telegram(message):
    token, chat = os.getenv("BOT_TOKEN","").strip(), os.getenv("CHAT_ID","").strip()
    if not token or not chat:
        log("TELEGRAM SKIP: missing secrets")
        return False
    try:
        r = http_json(TELEGRAM_API + "/bot" + token + "/sendMessage",
                      "POST", {"chat_id":chat,"text":message,"disable_web_page_preview":True})
        return bool(r.get("ok"))
    except Exception as e:
        log("TELEGRAM ERROR: " + str(e)[:160])
        return False

def github_headers():
    token = os.getenv("GITHUB_TOKEN","").strip()
    return {"Authorization":"Bearer " + token,
            "Accept":"application/vnd.github+json",
            "X-GitHub-Api-Version":"2022-11-28"}

def load_journal():
    repo = os.getenv("GITHUB_REPOSITORY","").strip()
    branch = os.getenv("GITHUB_REF_NAME","main").strip()
    if not repo or not os.getenv("GITHUB_TOKEN","").strip():
        raise RuntimeError("GITHUB_REPOSITORY/GITHUB_TOKEN missing")
    url = "https://api.github.com/repos/{}/contents/{}".format(repo, urllib.parse.quote(OUTCOME_FILE))
    try:
        data = http_json(url + "?ref=" + urllib.parse.quote(branch), headers=github_headers())
        raw = base64.b64decode(data.get("content","")).decode("utf-8")
        obj = json.loads(raw) if raw.strip() else {"signals":[]}
        return url, branch, obj, data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return url, branch, {"signals":[]}, None
        raise

def save_journal(url, branch, obj, sha):
    body = {"message":"Update ARAD signal outcomes",
            "content":base64.b64encode(json.dumps(obj,ensure_ascii=False,indent=2).encode("utf-8")).decode("ascii"),
            "branch":branch}
    if sha: body["sha"] = sha
    r = http_json(url, "PUT", body, github_headers())
    return bool(r.get("content"))

def parse_candles(payload):
    data = payload.get("data", payload) if isinstance(payload,dict) else payload
    out = []
    if isinstance(data,dict):
        ts = data.get("time") or data.get("timestamp") or []
        oo, hh, ll = data.get("open") or [], data.get("high") or [], data.get("low") or []
        cc = data.get("close") or []
        n = min(len(ts),len(oo),len(hh),len(ll),len(cc))
        for i in range(n):
            t = int(float(ts[i])); t = t//1000 if t > 10**12 else t
            vals = [float(oo[i]),float(hh[i]),float(ll[i]),float(cc[i])]
            if t > 0 and min(vals) > 0: out.append({"time":t,"open":vals[0],"high":vals[1],"low":vals[2],"close":vals[3]})
    elif isinstance(data,list):
        for row in data:
            try:
                if isinstance(row,dict):
                    t = int(float(row.get("time",row.get("timestamp",row.get("t")))))
                    vals = [float(row.get("open",row.get("o"))),float(row.get("high",row.get("h"))),float(row.get("low",row.get("l"))),float(row.get("close",row.get("c")))]
                else:
                    t = int(float(row[0])); vals = [float(row[1]),float(row[2]),float(row[3]),float(row[4])]
                t = t//1000 if t > 10**12 else t
                if t > 0 and min(vals) > 0: out.append({"time":t,"open":vals[0],"high":vals[1],"low":vals[2],"close":vals[3]})
            except Exception: pass
    return sorted({c["time"]:c for c in out}.values(), key=lambda x:x["time"])

def get_candles(symbol, start):
    end = int(time.time())
    path = "/api/v1/contract/kline/" + urllib.parse.quote(symbol, safe="")
    url = BASE_URL + path + "?" + urllib.parse.urlencode({"interval":"Min5","start":start,"end":end})
    data = http_json(url)
    return [c for c in parse_candles(data) if c["time"] >= start and c["time"] + TIMEFRAME_SECONDS <= end]

def check_signal(sig, candles):
    direction = sig["direction"]
    entry, stop = float(sig["entry"]), float(sig["sl"])
    targets = [float(x) for x in sig.get("tp",[])]
    for c in candles:
        if direction == "LONG":
            stop_hit = c["low"] <= stop
            hits = [i+1 for i,t in enumerate(targets) if c["high"] >= t]
        else:
            stop_hit = c["high"] >= stop
            hits = [i+1 for i,t in enumerate(targets) if c["low"] <= t]
        if stop_hit and hits:
            return {"status":"CLOSED","result":"AMBIGUOUS_SAME_CANDLE","max_tp":max(hits),"exit_candle":c["time"]}
        if stop_hit:
            return {"status":"CLOSED","result":"LOSS_SL","max_tp":0,"exit_candle":c["time"]}
        if hits:
            return {"status":"CLOSED","result":"WIN_TP1","max_tp":max(hits),"exit_candle":c["time"]}
    return None

def fmt_price(x):
    x = float(x)
    if x >= 1000: return "{:,.4f}".format(x)
    if x >= 1: return "{:.6f}".format(x)
    if x >= 0.01: return "{:.8f}".format(x)
    return "{:.10f}".format(x)

def main():
    url, branch, journal, sha = load_journal()
    signals = journal.get("signals",[])
    now = int(time.time())
    changed, resolved = False, []
    opened = [s for s in signals if isinstance(s,dict) and s.get("status") == "OPEN"]
    log("OUTCOME TRACKER | open signals: {}".format(len(opened)))
    for sig in opened:
        age = now - int(sig.get("created_at",now))
        if age > MAX_AGE_SECONDS:
            sig.update({"status":"CLOSED","result":"EXPIRED_24H","exit_candle":now})
            changed = True
            resolved.append(sig)
            continue
        try:
            candles = get_candles(sig["symbol"], int(sig["first_candle_time"]))
            result = check_signal(sig,candles)
            if result:
                sig.update(result)
                changed = True
                resolved.append(sig)
                log("RESOLVED {} {} => {}".format(sig["symbol"],sig["direction"],sig["result"]))
        except Exception as e:
            log("CHECK ERROR {}: {}".format(sig.get("symbol"),str(e)[:160]))
        time.sleep(0.15)

    if changed:
        journal["signals"] = signals[-500:]
        if not save_journal(url,branch,journal,sha):
            raise RuntimeError("Could not save outcome journal")
        completed = [s for s in signals if s.get("status")=="CLOSED"]
        wins = sum(1 for s in completed if s.get("result")=="WIN_TP1")
        losses = sum(1 for s in completed if s.get("result")=="LOSS_SL")
        ambiguous = sum(1 for s in completed if s.get("result")=="AMBIGUOUS_SAME_CANDLE")
        expired = sum(1 for s in completed if s.get("result")=="EXPIRED_24H")
        decisive = wins + losses
        rate = (100.0*wins/decisive) if decisive else 0.0
        for s in resolved:
            result = s["result"]
            if result == "WIN_TP1":
                label = "برد: TP1 قبل از SL"
            elif result == "LOSS_SL":
                label = "باخت: SL قبل از TP1"
            elif result == "AMBIGUOUS_SAME_CANDLE":
                label = "نامشخص: TP و SL در یک کندل ۵دقیقه‌ای لمس شدند؛ در آمار برد حساب نمی‌شود"
            else:
                label = "منقضی: طی ۲۴ ساعت نه TP1 و نه SL لمس نشد"
            tp_text = " | بیشترین تارگت لمس‌شده: TP{}".format(s.get("max_tp",0)) if s.get("max_tp",0) else ""
            msg = ("📊 ARAD | نتیجه سیگنال\n"
                   "نماد: {} | جهت: {} | نوع: {}\n"
                   "ورود: {} | نتیجه: {}{}\n"
                   "آمار قطعی: برد {} / باخت {} | نرخ برد: {:.1f}%\n"
                   "موارد مبهم: {} | منقضی: {}").format(
                       s["symbol"],s["direction"],s.get("alert_type","?"),fmt_price(s["entry"]),
                       label,tp_text,wins,losses,rate,ambiguous,expired)
            if not telegram(msg):
                log("Outcome saved but Telegram notification failed")
        log("JOURNAL SAVED | wins={} losses={} ambiguous={} expired={}".format(wins,losses,ambiguous,expired))
    else:
        log("No signal reached TP1/SL; journal unchanged")

if __name__ == "__main__":
    main()
