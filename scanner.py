import os, json, time, requests
from datetime import datetime, timezone, timedelta

TOKEN   = os.environ["TG_TOKEN"]
CHAT    = os.environ["TG_CHAT"]
BAR     = os.getenv("BAR", "1H")
PERIOD  = 14
OVERSOLD, OVERBOUGHT = 30, 70
LIMIT   = 300
OKX     = "https://www.okx.com"
STATE_F = "scanner_state.json"
TZ      = timezone(timedelta(hours=7))

S = requests.Session()
S.headers.update({"User-Agent": "rsi-scanner"})


def load_state():
    try:
        with open(STATE_F, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_F, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def get_symbols():
    """ดึงคู่ -USDT-SWAP ทั้งหมด เรียงตามมูลค่าซื้อขาย USDT จากมากไปน้อย"""
    for attempt in range(3):
        try:
            r = S.get(f"{OKX}/api/v5/market/tickers",
                      params={"instType": "SWAP"}, timeout=25)
            data = r.json().get("data", [])
            if not data:
                raise ValueError("empty tickers")
            rows = []
            for d in data:
                inst = d["instId"]
                if not inst.endswith("-USDT-SWAP"):
                    continue
                try:
                    vol = float(d.get("volCcyQuote24h") or 0)
                except (TypeError, ValueError):
                    vol = 0.0
                rows.append((inst, vol))
            rows.sort(key=lambda x: x[1], reverse=True)
            return [r[0] for r in rows]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    return []


def get_closes(inst_id):
    """คืนราคาปิดของ 'แท่งที่ปิดสมบูรณ์แล้ว' เท่านั้น (เก่า -> ใหม่)"""
    r = S.get(f"{OKX}/api/v5/market/candles",
              params={"instId": inst_id, "bar": BAR, "limit": LIMIT},
              timeout=25)
    data = r.json().get("data", [])
    closed = [c for c in data if len(c) > 8 and c[8] == "1"]
    if len(closed) < PERIOD * 4:
        return []
    return [float(c[4]) for c in reversed(closed)]


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


def tv_link(inst_id):
    sym = inst_id.replace("-USDT-SWAP", "") + "USDT.P"
    return f"https://www.tradingview.com/chart/?symbol=OKX%3A{sym}"


def zone_of(v):
    if v <= OVERSOLD:
        return "OS"
    if v >= OVERBOUGHT:
        return "OB"
    return "MID"


def send(text):
    for i in range(0, len(text), 3800):
        S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
               json={"chat_id": CHAT, "text": text[i:i + 3800],
                     "parse_mode": "HTML",
                     "disable_web_page_preview": True}, timeout=25)
        time.sleep(0.3)


def main():
    state = load_state()
    symbols = get_symbols()
    total = len(symbols)

    new_ob, new_os = [], []
    skipped = 0
    errors = {}

    for inst in symbols:
        try:
            closes = get_closes(inst)
            if not closes:
                skipped += 1
                continue
            v = rsi(closes)
            if v is None:
                skipped += 1
                continue

            z = state.get(inst)
            now_z = zone_of(v)

            if now_z == "OS" and z != "OS":
                new_os.append((inst, v, closes[-1]))
            elif now_z == "OB" and z != "OB":
                new_ob.append((inst, v, closes[-1]))

            state[inst] = now_z

        except Exception as e:
            skipped += 1
            key = type(e).__name__
            errors[key] = errors.get(key, 0) + 1
        time.sleep(0.15)

    save_state(state)

    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    lines = [
        "📊 <b>RSI Scanner · OKX PERP</b>",
        f"🕐 {now} (ไทย)",
        f"TF <code>{BAR}</code> · RSI({PERIOD}) · แท่งปิดแล้ว",
    ]

    hit = len(new_ob) + len(new_os)

    if new_ob:
        lines.append("\n🔴 <b>OVERBOUGHT</b>")
        new_ob.sort(key=lambda x: -x[1])
        for inst, v, price in new_ob:
            name = inst.replace("-USDT-SWAP", "")
            lines.append(f'<a href="{tv_link(inst)}">{name}</a>  '
                         f'RSI <b>{v:.1f}</b> | {price:g}')

    if new_os:
        lines.append("\n🟢 <b>OVERSOLD</b>")
        new_os.sort(key=lambda x: x[1])
        for inst, v, price in new_os:
            name = inst.replace("-USDT-SWAP", "")
            lines.append(f'<a href="{tv_link(inst)}">{name}</a>  '
                         f'RSI <b>{v:.1f}</b> | {price:g}')

    if hit == 0:
        lines.append(f"\n✅ สแกน {total} คู่ · ไม่มีสัญญาณใหม่")
    else:
        lines.append(f"\nรวม <b>{hit}</b> สัญญาณใหม่ · สแกน {total} คู่")

    if skipped:
        lines.append(f"<i>ข้าม {skipped} คู่</i>")
    if errors:
        detail = ", ".join(f"{k}×{v}" for k, v in errors.items())
        lines.append(f"<i>error: {detail}</i>")

    send("\n".join(lines))


if __name__ == "__main__":
    main()
