"""
Universe expander. Turns tokens like @SP500 / @NASDAQ100 in the watchlist
into their full constituent ticker lists (fetched live from Wikipedia).
Manual tickers (AAPL, DELTA.BK, 0700.HK ...) pass through untouched.
"""
import io, urllib.request
import pandas as pd

WIKI = {
    "@SP500":     "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "@NASDAQ100": "https://en.wikipedia.org/wiki/Nasdaq-100",
}

def _fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (StockScout)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")

def _norm(t):
    # yfinance uses '-' not '.' for share classes: BRK.B -> BRK-B
    return str(t).strip().upper().replace(".", "-")

def _tickers_from_wiki(url):
    tables = pd.read_html(io.StringIO(_fetch_html(url)))
    for df in tables:
        for col in ("Symbol", "Ticker"):
            if col in df.columns:
                out = [_norm(s) for s in df[col].astype(str) if s and s != "nan"]
                if len(out) > 20:            # sanity: real constituent table
                    return out
    return []

def expand(tickers):
    """Replace @TOKENs with constituents; keep manual tickers; dedupe (order kept)."""
    out = []
    for t in tickers:
        key = t.strip().upper()
        if key in WIKI:
            try:
                names = _tickers_from_wiki(WIKI[key])
                print(f"  expanded {key} -> {len(names)} tickers")
                out.extend(names)
            except Exception as e:
                print(f"  ! failed to expand {key}: {e}")
        else:
            out.append(t)                    # manual ticker, untouched
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return uniq
