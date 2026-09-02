import os, time, requests

TOKEN   = os.environ["TG_TOKEN"]
CHAT    = os.environ["TG_CHAT"]
BAR     = os.getenv("BAR", "1H")
PERIOD  = 14
OVERSOLD, OVERBOUGHT = 30, 70
TOP_N   = 60
OKX     = "https://www.okx.com"

S = requests.Session()
S.headers.update({"User-Agent": "rsi-scanner"})


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
            vol = float(d.get("volCcy24h") or 0)
        except ValueError:
            vol = 0
        rows.append((inst, vol))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in rows[:TOP_N]]


def get_closes(inst_id):
    r = S.get(f"{OKX}/api/v5/market/candles",
              params={"instId": inst_id, "bar": BAR, "limit": 100}, timeout=20)
    data = r.json().get("data", [])
    if len(data) < PERIOD + 2:
        return []
    return [float(c[4]) for c in reversed(data)]


def rsi(closes, period=PERIOD):
    if len(closes) < period + 1:
        return None
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0); loss += max(-d, 0)
    ag, al = gain / period, loss / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def scan(inst_type, label):
    hits = []
    for inst in get_symbols(inst_type):
        try:
            closes = get_closes(inst)
            v = rsi(closes)
            if v is None:
                continue
            if v <= OVERSOLD:
                hits.append(("🟢 OVERSOLD", inst, v, closes[-1]))
            elif v >= OVERBOUGHT:
                hits.append(("🔴 OVERBOUGHT", inst, v, closes[-1]))
        except Exception:
            pass
        time.sleep(0.12)
    return label, hits


def send(text):
    for i in range(0, len(text), 3800):
        S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
               json={"chat_id": CHAT, "text": text[i:i + 3800],
                     "parse_mode": "HTML",
                     "disable_web_page_preview": True}, timeout=20)


def main():
    lines = [f"<b>📊 RSI Scanner OKX</b> · TF <code>{BAR}</code> · RSI({PERIOD})"]
    total = 0
    for inst_type, label in (("SPOT", "SPOT"), ("SWAP", "PERP")):
        label, hits = scan(inst_type, label)
        lines.append(f"\n<b>— {label} —</b>")
        if not hits:
            lines.append("ไม่พบสัญญาณ")
            continue
        hits.sort(key=lambda x: x[2])
        for tag, inst, v, price in hits:
            total += 1
            lines.append(f"{tag} <code>{inst}</code>  RSI {v:.1f}  |  {price:g}")
    lines.append(f"\nรวม <b>{total}</b> สัญญาณ")
    send("\n".join(lines))


if __name__ == "__main__":
    main()
