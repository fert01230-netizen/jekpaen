import os, json, time, requests
from datetime import datetime, timezone, timedelta

TOKEN      = os.environ["TG_TOKEN"]
CHAT       = os.environ["TG_CHAT"]
BAR        = os.getenv("BAR", "1H")
PERIOD     = 14
OVERSOLD, OVERBOUGHT = 30, 70
ENTRY_LO, ENTRY_HI   = 45, 65
TOP_N      = 60
MIN_VOL    = 1_000_000
BIAS_FILE  = "bias_state.json"
OKX        = "https://www.okx.com"
TH         = timezone(timedelta(hours=7))

S = requests.Session()
S.headers.update({"User-Agent": "swing-bias"})


def get_symbols(inst_type):
    r = S.get(f"{OKX}/api/v5/market/tickers",
              params={"instType": inst_type}, timeout=20)
    data = r.json().get("data", [])
    rows = []
    for d in data:
        inst = d["instId"]
        if inst_type == "SPOT" and not inst.endswith("-USDT"):
            continue
        if inst_type == "SWAP" and not inst.endswith("-USDT-SWAP"):
            continue
        try:
            vol   = float(d.get("volCcy24h") or 0)
            price = float(d.get("last") or 0)
        except ValueError:
            continue
        if vol < MIN_VOL:
            continue
        rows.append((inst, vol, price))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [(r[0], r[2]) for r in rows[:TOP_N]]


def closes(inst):
    r = S.get(f"{OKX}/api/v5/market/candles",
              params={"instId": inst, "bar": BAR, "limit": 120}, timeout=20)
    rows = r.json().get("data", [])
    if len(rows) < PERIOD + 2:
        return []
    return [float(c[4]) for c in reversed(rows)]


def rsi(cs, period=PERIOD):
    if len(cs) < period + 1:
        return None
    g = l = 0.0
    for i in range(1, period + 1):
        d = cs[i] - cs[i-1]
        g += max(d, 0); l += max(-d, 0)
    ag, al = g / period, l / period
    for i in range(period + 1, len(cs)):
        d = cs[i] - cs[i-1]
        ag = (ag * (period-1) + max(d, 0)) / period
        al = (al * (period-1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def load_bias():
    if os.path.exists(BIAS_FILE):
        try:
            with open(BIAS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_bias(b):
    with open(BIAS_FILE, "w") as f:
        json.dump(b, f, indent=2)


def update_bias(inst, val, bias):
    rec = bias.get(inst, {"bias": "NEUTRAL", "since": 0,
                          "trigger": None, "notified": False})
    prev  = rec["bias"]
    event = None

    if val < OVERSOLD and prev != "SHORT":
        rec = {"bias": "SHORT", "since": time.time(),
               "trigger": round(val, 1), "notified": False}
        event = "FLIP_SHORT"
    elif val > OVERBOUGHT and prev != "LONG":
        rec = {"bias": "LONG", "since": time.time(),
               "trigger": round(val, 1), "notified": False}
        event = "FLIP_LONG"
    elif prev in ("SHORT", "LONG") and ENTRY_LO <= val <= ENTRY_HI \
            and not rec.get("notified"):
        rec["notified"] = True
        event = "ENTRY_" + prev

    bias[inst] = rec
    return event


def waited(since):
    if not since:
        return "-"
    h = (time.time() - since) / 3600
    return f"{h:.0f} ชม." if h >= 1 else f"{h*60:.0f} นาที"


def send(text):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT, "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True}, timeout=20)


def main():
    bias = load_bias()
    ev = {"FLIP_SHORT": [], "FLIP_LONG": [], "ENTRY_SHORT": [], "ENTRY_LONG": []}

    pairs = get_symbols("SWAP")

    print(f"scanning {len(pairs)} pairs on {BAR}")

    for inst, price in pairs:
        try:
            cs = closes(inst)
            if not cs:
                continue
            val = rsi(cs)
            if val is None:
                continue
            e = update_bias(inst, val, bias)
            if e:
                ev[e].append((inst, val, price, bias[inst]))
        except Exception as err:
            print(f"skip {inst}: {err}")
        time.sleep(0.06)

    save_bias(bias)

    blocks = [
        ("FLIP_SHORT",  "🔴 <b>เข้าโหมดรอชอต</b> (RSI &lt; 30)"),
        ("FLIP_LONG",   "🟢 <b>เข้าโหมดรอลอง</b> (RSI &gt; 70)"),
        ("ENTRY_SHORT", "⚡ <b>ถึงโซนเข้าไม้ · SHORT</b>"),
        ("ENTRY_LONG",  "⚡ <b>ถึงโซนเข้าไม้ · LONG</b>"),
    ]

    lines, total = [], 0
    for key, title in blocks:
        rows = sorted(ev[key], key=lambda x: x[1])
        if not rows:
            continue
        lines.append(f"\n{title}")
        for inst, val, price, rec in rows:
            name  = inst.replace("-SWAP", " ⓟ")
            extra = ""
            if key.startswith("ENTRY"):
                extra = f"  · รอมา {waited(rec['since'])} · trig {rec['trigger']}"
            lines.append(f"<code>{name}</code>  RSI {val:.1f}  |  {price:g}{extra}")
            total += 1

    if total == 0:
        print("no events")
        return

    now = datetime.now(TH).strftime("%H:%M")
    msg = (f"<b>🎯 Swing Bias · TF {BAR} · RSI({PERIOD})</b>\n"
           + "\n".join(lines)
           + f"\n\n<i>รวม {total} รายการ · {now}</i>")
    send(msg)
    print(f"sent {total}")


if __name__ == "__main__":
    main()
