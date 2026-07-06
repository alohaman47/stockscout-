"""
Core trend-clarity scoring engine. Pure numpy/pandas, no external TA libs.
Testable independently of data source.
"""
import numpy as np
import pandas as pd


# ---------- indicators ----------
def sma(s, n):
    return s.rolling(n).mean()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100/(1+rs)

def atr(df, n=14):
    h, l, c = df['High'], df['Low'], df['Close']
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def adx(df, n=14):
    h, l, c = df['High'], df['Low'], df['Close']
    up = h.diff(); dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


# ---------- clarity / momentum core (Clenow-style) ----------
def regression_momentum(close, lookback=90):
    """
    Fit linear regression to log(price). Return:
      annualized_slope: yearly % implied by daily log slope
      r2: how well the trend fits a straight line (0..1) = 'clarity'
      score: annualized_slope * r2  (sign = direction, magnitude = clean steepness)
    """
    y = np.log(close.tail(lookback).values)
    if len(y) < lookback or np.any(~np.isfinite(y)):
        return np.nan, np.nan, np.nan
    x = np.arange(len(y))
    b, a = np.polyfit(x, y, 1)           # slope, intercept (daily log units)
    resid = y - (a + b*x)
    ss_res = np.sum(resid**2)
    ss_tot = np.sum((y - y.mean())**2)
    r2 = 1 - ss_res/ss_tot if ss_tot > 0 else 0.0
    ann_raw = np.exp(b * 252) - 1        # annualized (continuously compounded)
    # clip: extrapolating a steep 90d slope to a year explodes; cap at +/-300%/yr
    ann = float(np.clip(ann_raw, -3.0, 3.0))
    return ann, r2, ann * r2


# ---------- Minervini-style trend template (graded 0..1) ----------
def trend_template(df):
    c = df['Close']
    s50, s150, s200 = sma(c,50), sma(c,150), sma(c,200)
    px = c.iloc[-1]
    hi52, lo52 = c.tail(252).max(), c.tail(252).min()
    s200_rising = s200.iloc[-1] > s200.iloc[-20]
    up = [
        px > s150.iloc[-1] and px > s200.iloc[-1],
        s150.iloc[-1] > s200.iloc[-1],
        s200_rising,
        s50.iloc[-1] > s150.iloc[-1] > s200.iloc[-1],
        px > s50.iloc[-1],
        px >= 1.30*lo52,
        px >= 0.75*hi52,
    ]
    s200_falling = s200.iloc[-1] < s200.iloc[-20]
    dn = [
        px < s150.iloc[-1] and px < s200.iloc[-1],
        s150.iloc[-1] < s200.iloc[-1],
        s200_falling,
        s50.iloc[-1] < s150.iloc[-1] < s200.iloc[-1],
        px < s50.iloc[-1],
        px <= 0.70*hi52,
        px <= 1.25*lo52,
    ]
    return sum(up)/len(up), sum(dn)/len(dn)


# ---------- 🦋 Hurst exponent (persistence vs mean-reversion) ----------
def hurst(close, max_lag=60):
    """
    Slope of log(std of lagged diffs) vs log(lag), on log price.
    H > 0.5 = trending/persistent · H < 0.5 = mean-reverting · ~0.5 = random walk.
    """
    y = np.log(np.asarray(close, dtype=float))
    y = y[np.isfinite(y)]
    if len(y) < max_lag + 20:
        return np.nan
    lags = np.arange(2, max_lag)
    tau = []
    for lag in lags:
        d = y[lag:] - y[:-lag]
        s = np.std(d)
        tau.append(s if s > 0 else 1e-9)
    slope = np.polyfit(np.log(lags), np.log(tau), 1)[0]
    return slope

def hurst_tag(h):
    if np.isnan(h):        return "—"
    if h >= 0.58:          return "ไปต่อ"      # persistent trend
    if h <= 0.42:          return "หลอก/เด้ง"  # mean-reverting
    return "สุ่ม"

# ---------- 🦋 Coiled-spring compression score ----------
def _pctrank(series, window):
    s = series.tail(window).dropna()
    if len(s) < 10:
        return np.nan
    cur = s.iloc[-1]
    return (s < cur).mean()          # 0=lowest in window, 1=highest

def coil(df, lookback=126):
    """0..1 compression score. High = tightly coiled (squeeze + volume dry-up + NR7)."""
    c, h, l = df['Close'], df['High'], df['Low']
    ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    bbw = (4*sd20)/ma20                          # Bollinger band width / mid
    atr_series = atr(df) / c                     # ATR normalized by price
    bbw_p = _pctrank(bbw, lookback)              # low = squeeze
    atr_p = _pctrank(atr_series, lookback)       # low = compressed
    rng = (h - l)
    nr7  = 1.0 if rng.iloc[-1] <= rng.tail(7).min() else 0.0
    vdry = np.nan
    if 'Volume' in df.columns and df['Volume'].tail(20).sum() > 0:
        v = df['Volume']
        vdry = v.tail(5).mean() / (v.tail(20).mean() + 1e-9)   # <1 = drying up
    parts = []
    if not np.isnan(bbw_p): parts.append(1 - bbw_p)
    if not np.isnan(atr_p): parts.append(1 - atr_p)
    parts.append(0.5 + 0.5*nr7)                  # NR7 nudges up
    if not np.isnan(vdry): parts.append(float(np.clip(1.2 - vdry, 0, 1)))
    score = float(np.mean(parts)) if parts else np.nan
    lean = "↑" if c.iloc[-1] > ma20.iloc[-1] else "↓"
    return dict(coil=score, bbw_p=bbw_p, atr_p=atr_p, nr7=bool(nr7),
                vdry=vdry, lean=lean)

def analyze(df, lookback=90):
    """df: OHLC(+Volume) daily. Returns dict of metrics."""
    if len(df) < 210:
        return None
    c = df['Close']
    ann, r2, mom = regression_momentum(c, lookback)
    up_t, dn_t = trend_template(df)
    adx_v = adx(df).iloc[-1]
    atr_v = atr(df).iloc[-1]
    rsi_v = rsi(c).iloc[-1]
    px = c.iloc[-1]
    hi20 = df['High'].tail(20).max()
    pull = (hi20 - px)/hi20 * 100         # % below 20d high (extension/pullback)
    direction = 'UP' if mom > 0 else 'DOWN'
    template = up_t if mom > 0 else dn_t
    h = hurst(c)
    coil_d = coil(df)
    return dict(price=px, direction=direction, mom=mom, ann=ann, r2=r2,
                template=template, adx=adx_v, atr=atr_v, rsi=rsi_v, pull_pct=pull,
                hurst=h, hurst_tag=hurst_tag(h), **coil_d)
