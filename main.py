#!/usr/bin/env python3
"""
StockScout — multi-market trend-clarity screener.
Scans a watchlist daily, scores each stock by trend cleanliness & strength,
ranks the clearest up / down trends, and pushes a report to Telegram.
Runs free on GitHub Actions. Data via yfinance (no API key).
"""
import os, time, datetime as dt
import yfinance as yf
from engine import analyze

# ---------- config (override via repo Secrets / env) ----------
def _env(name, default):
    """Return env var, but fall back to default if unset OR empty string
    (GitHub passes '' for undefined `vars.*`)."""
    v = os.getenv(name)
    return v if v not in (None, "") else default

TOP_N     = int(_env("TOP_N", "8"))
LOOKBACK  = int(_env("LOOKBACK", "90"))
MIN_R2    = float(_env("MIN_R2", "0.55"))   # trend-clarity gate
MIN_ADX   = float(_env("MIN_ADX", "18"))    # trend-strength gate
MIN_TMPL  = float(_env("MIN_TMPL", "0.70")) # MA-stack agreement gate
A_MIN_R2  = float(_env("A_MIN_R2", "0.70"))  # ⭐ A+ clarity bar
A_MIN_ADX = float(_env("A_MIN_ADX", "25"))   # ⭐ A+ strength bar
COIL_MIN  = float(_env("COIL_MIN", "0.60"))   # 🦋 min compression to list
RIPPLE_LAG     = int(_env("RIPPLE_LAG", "5"))       # 🦋 max lead-lag days
RIPPLE_MINCORR = float(_env("RIPPLE_MINCORR", "0.30"))  # 🦋 min corr to report
RIPPLE_MAX_N   = int(_env("RIPPLE_MAX_N", "60"))    # 🦋 skip ripple if universe bigger (O(n^2))
CHUNK          = int(_env("CHUNK", "100"))          # batch size for yfinance download
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

MARKET_NAME = {"": "US", ".BK": "ไทย SET", ".HK": "ฮ่องกง",
               ".SS": "จีน (เซี่ยงไฮ้)", ".SZ": "จีน (เซินเจิ้น)",
               ".T": "ญี่ปุ่น", ".KS": "เกาหลี", ".TW": "ไต้หวัน"}

def market_of(ticker):
    for suf in [".BK",".HK",".SS",".SZ",".T",".KS",".TW"]:
        if ticker.upper().endswith(suf):
            return MARKET_NAME[suf]
    return MARKET_NAME[""]

# ---------- load watchlist ----------
def load_watchlist(path="watchlist.txt"):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            t = line.split("#")[0].strip()
            if t:
                out.append(t)
    return out

# ---------- fetch + score ----------
def _extract(data, t, single):
    """Pull one ticker's OHLCV frame out of a yf.download result."""
    if single:
        df = data.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    else:
        if t not in data.columns.get_level_values(0):
            return None
        df = data[t].copy()
    df = df.dropna(how="all")
    if df.empty or "Close" not in df.columns:
        return None
    return df

def scan(tickers):
    rows, panel = [], {}
    for i in range(0, len(tickers), CHUNK):
        batch = tickers[i:i+CHUNK]
        try:
            data = yf.download(batch, period="2y", interval="1d", auto_adjust=True,
                               progress=False, group_by="ticker", threads=True)
        except Exception as e:
            print(f"  ! batch {i//CHUNK+1} download error: {e}"); continue
        if data is None or data.empty:
            print(f"  ! batch {i//CHUNK+1}: no data"); continue
        single = len(batch) == 1
        for t in batch:
            try:
                df = _extract(data, t, single)
                if df is None:
                    continue
                panel[t] = df["Close"]
                r = analyze(df, LOOKBACK)
                if r is None:
                    continue
                r["ticker"] = t
                r["market"] = market_of(t)
                rows.append(r)
            except Exception as e:
                print(f"  ! {t}: {e}")
        print(f"  batch {i//CHUNK+1}/{(len(tickers)+CHUNK-1)//CHUNK}: "
              f"{len(rows)} scored so far")
        time.sleep(1.5)
    return rows, panel

def entry_tag(r):
    if r["direction"] == "UP":
        if r["pull_pct"] < 1 and r["rsi"] > 72:  return "ยืดเกิน—รอย่อ"
        if 2 <= r["pull_pct"] <= 12 and r["rsi"] < 60: return "โซนย่อ—น่าเข้า"
        return "เทรนด์ขึ้น—เฝ้า"
    else:
        if r["pull_pct"] < 1 and r["rsi"] < 28:  return "ร่วงเกิน—รอเด้ง"
        return "เทรนด์ลง—เฝ้า"

def passes(r):
    return (r["r2"] >= MIN_R2 and r["adx"] >= MIN_ADX
            and r["template"] >= MIN_TMPL)

def is_entry_zone(r):
    """Ideal timing: pull back in an uptrend, or sell a bounce in a downtrend."""
    if r["direction"] == "UP":
        return 2 <= r["pull_pct"] <= 12 and r["rsi"] < 60
    return r["pull_pct"] <= 6 and 40 <= r["rsi"] <= 62

def is_a_plus(r):
    """⭐ A+: clean + persistent + strong + at a good entry, all at once."""
    return (not _nan(r.get("r2")) and r["r2"] >= A_MIN_R2
            and r.get("hurst_tag") == "ไปต่อ"
            and not _nan(r.get("adx")) and r["adx"] >= A_MIN_ADX
            and is_entry_zone(r))

# ---------- markdown report ----------
def table(rows):
    if not rows:
        return "_— ไม่มีตัวผ่านเกณฑ์ —_\n"
    head = ("| Ticker | ตลาด | Score | R² | 🦋H | ADX | ปี% | ราคา | SL 2ATR (%) | จังหวะ |\n"
            "|---|---|--:|--:|:--:|--:|--:|--:|--:|---|\n")
    body = ""
    for r in rows:
        stop = r["price"]-2*r["atr"] if r["direction"]=="UP" else r["price"]+2*r["atr"]
        risk = abs(r["price"]-stop)/r["price"]*100
        hcell = f"{r['hurst']:.2f} {r['hurst_tag']}" if not _nan(r['hurst']) else "—"
        star = "⭐ " if is_a_plus(r) else ""
        body += (f"| {star}**{r['ticker']}** | {r['market']} | {r['mom']:+.2f} | "
                 f"{r['r2']:.2f} | {hcell} | {r['adx']:.0f} | {r['ann']*100:+.0f}% | "
                 f"{r['price']:.2f} | {stop:.2f} ({risk:.1f}%) | {entry_tag(r)} |\n")
    return head + body

def _nan(x):
    try:    return x != x
    except: return True

def coil_section(rows):
    cand = [r for r in rows if not _nan(r.get("coil")) and r["coil"] >= COIL_MIN]
    cand.sort(key=lambda x:-x["coil"])
    cand = cand[:TOP_N]
    if not cand:
        return "_— ไม่มีตัวขดตัวเข้าเกณฑ์ —_\n"
    head = ("| Ticker | ตลาด | Coil | เอียง | BBW%ile | ATR%ile | NR7 | วอลุ่ม | 🦋H |\n"
            "|---|---|--:|:--:|--:|--:|:--:|--:|--:|\n")
    body = ""
    for r in cand:
        bb = "—" if _nan(r['bbw_p']) else f"{r['bbw_p']*100:.0f}"
        at = "—" if _nan(r['atr_p']) else f"{r['atr_p']*100:.0f}"
        vd = "—" if _nan(r['vdry']) else f"{r['vdry']:.2f}×"
        nr = "✔" if r['nr7'] else ""
        hh = "—" if _nan(r['hurst']) else f"{r['hurst']:.2f}"
        body += (f"| **{r['ticker']}** | {r['market']} | {r['coil']:.2f} | {r['lean']} | "
                 f"{bb} | {at} | {nr} | {vd} | {hh} |\n")
    return head + body

def ripple_section(relations, rows_by_ticker):
    if not relations:
        return "_— ไม่พบความสัมพันธ์นำ-ตามที่ชัดพอ —_\n"
    active = [x for x in relations if x.get("active")]
    head = ("| ตัวนำ (leader) | → ตัวตาม (follower) | ตามหลัง | corr | ตัวนำเพิ่งขยับ |\n"
            "|---|---|:--:|--:|--:|\n")
    body = ""
    show = (active or relations)
    for x in show:
        flag = "🔥" if x.get("active") else ""
        body += (f"| **{x['leader']}** | {x['follower']} | ~{x['lag']}d | "
                 f"{x['corr']:.2f} | {x['leader_recent_pct']:+.1f}% {flag} |\n")
    note = ("\n> 🔥 = ตัวนำเพิ่งขยับ ≥2% ในช่วง lead → จับตาตัวตาม · "
            "ความสัมพันธ์รายวันอ่อนและไม่เสถียรโดยธรรมชาติ ใช้เป็น watch-hint เท่านั้น\n")
    return head + body + note

def build_report(rows, panel=None):
    good = [r for r in rows if passes(r)]
    ups  = sorted([r for r in good if r["direction"]=="UP"],   key=lambda x:-x["mom"])[:TOP_N]
    dns  = sorted([r for r in good if r["direction"]=="DOWN"], key=lambda x: x["mom"])[:TOP_N]
    aplus = [r for r in good if is_a_plus(r)]
    aplus.sort(key=lambda x: (x["direction"] != "UP", -abs(x["mom"])))  # ups first, strongest first
    # per-market breakdown of what was actually scanned
    from collections import Counter
    mk = Counter(r["market"] for r in rows)
    mk_str = " · ".join(f"{m} {n}" for m, n in mk.most_common())
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    md  = f"# 📡 StockScout — {today}\n\n"
    md += (f"สแกน **{len(rows)}** ตัว ({mk_str}) · ผ่านเกณฑ์ **{len(good)}** · "
           f"⭐ A+ **{len(aplus)}** "
           f"(R²≥{MIN_R2} · ADX≥{MIN_ADX:.0f} · template≥{MIN_TMPL}) · "
           f"🦋H=Hurst (ไปต่อ/หลอก)\n\n")

    # ⭐ A+ setups — the only section that says "พร้อมเข้า"
    md += "## ⭐ A+ Setups — พร้อมเข้าที่สุด\n\n"
    if aplus:
        md += table(aplus)
        md += ("\n> เกณฑ์ A+ = R²≥%.2f **และ** Hurst ไปต่อ **และ** ADX≥%.0f **และ** อยู่โซนเข้า "
               "→ ผ่านครบ 4 ชั้นพร้อมกัน ยังต้องยืนยันด้วยกราฟ+วาง SL เอง\n" % (A_MIN_R2, A_MIN_ADX))
    else:
        md += ("_— วันนี้ยังไม่มีตัวเข้าเกณฑ์ A+ —_\n\n"
               "> ปกติมาก ส่วนใหญ่ของวันจะว่าง เพราะรอ**จังหวะย่อ**ของตัวคุณภาพดี "
               "ดูตารางเทรนด์ข้างล่างแล้วเฝ้าตัวที่ Hurst=ไปต่อ + R² สูง รอมันพลิกเป็น 'โซนย่อ—น่าเข้า'\n")
    md += "\n"

    md += f"## 🟢 ขาขึ้นชัดสุด (Top {len(ups)})\n\n" + table(ups) + "\n"
    md += f"## 🔴 ขาลงชัดสุด (Top {len(dns)})\n\n" + table(dns) + "\n"

    # 🦋 butterfly modes
    md += "## 🦋 จุดชนวน — ขดตัวรอระเบิด (Coiled Spring)\n\n"
    md += coil_section(rows) + "\n"
    if panel and len(panel) <= RIPPLE_MAX_N:
        from butterfly import ripple_network
        rel = ripple_network(panel, max_lag=RIPPLE_LAG,
                             min_corr=RIPPLE_MINCORR, top=TOP_N)
        md += "## 🦋 แรงกระเพื่อม — ตัวนำนำตัวตาม (Ripple)\n\n"
        md += ripple_section(rel, {r["ticker"]: r for r in rows}) + "\n"
    elif panel:
        md += "## 🦋 แรงกระเพื่อม — ตัวนำนำตัวตาม (Ripple)\n\n"
        md += (f"_— ปิดอัตโนมัติ: universe ใหญ่ ({len(panel)} ตัว > {RIPPLE_MAX_N}) "
               f"การวิเคราะห์ lead-lag เป็น O(n²) จะช้าเกินไป —_\n\n")

    md += ("---\n*Score = ความชันรายปี × R² · Coil = ระดับการบีบอัด (สูง=ขดแน่น) · "
           "Hurst>0.5=เทรนด์ไปต่อ, <0.5=มีแนวโน้มเด้งกลับ · "
           "เครื่องมือจัดระเบียบข้อมูล ไม่ใช่คำแนะนำการลงทุน*\n")
    return md

# ---------- outputs: file + GitHub summary (+ optional telegram) ----------
def write_outputs(md):
    # 1) save latest + dated archive in repo
    os.makedirs("reports", exist_ok=True)
    day = dt.date.today().isoformat()
    for p in ("reports/latest.md", f"reports/{day}.md"):
        with open(p, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"  wrote {p}")
    # 2) render on the Actions run page (Job Summary)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
        print("  wrote job summary")
    # 3) optional telegram (only if creds set)
    if BOT_TOKEN and CHAT_ID:
        send_telegram(md)
    # 4) always echo to log so it's visible even without commit
    print("\n" + md)

def send_telegram(md):
    import urllib.request, urllib.parse, json
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for i in range(0, len(md), 3800):
        data = urllib.parse.urlencode({
            "chat_id": CHAT_ID, "text": md[i:i+3800],
            "parse_mode": "Markdown", "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=data)).read()
        except Exception as e:
            print(f"  telegram error: {e}")

def main():
    from universe import expand
    tickers = expand(load_watchlist())
    print(f"scanning {len(tickers)} tickers ...")
    rows, panel = scan(tickers)
    if not rows:
        write_outputs("# 📡 StockScout\n\nไม่มีข้อมูลที่ใช้ได้วันนี้\n"); return
    write_outputs(build_report(rows, panel))

if __name__ == "__main__":
    main()
