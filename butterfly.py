"""
🦋 Ripple network — lead-lag detection across the watchlist.
Finds pairs where one ticker's move statistically precedes another's,
so a recent move in a 'leader' can flag which 'followers' may ripple next.

NOTE: daily lead-lag correlations are weak and unstable by nature.
Treat output as exploratory watch-hints, not a hard edge.
"""
import numpy as np
import pandas as pd


def build_returns(panel, min_overlap=120):
    """panel: dict ticker -> Close Series (datetime index). Returns aligned log-return df."""
    cols = {}
    for t, s in panel.items():
        s = s.dropna()
        if len(s) >= min_overlap:
            cols[t] = np.log(s).diff()
    if not cols:
        return pd.DataFrame()
    rets = pd.DataFrame(cols).dropna(how="all")
    return rets


def ripple_network(panel, min_overlap=120, max_lag=5, min_corr=0.30, top=10):
    """
    For each follower, find the single best leader (highest lagged correlation,
    lag 1..max_lag). Returns list of dicts sorted by correlation.
    """
    rets = build_returns(panel, min_overlap)
    if rets.empty or rets.shape[1] < 2:
        return []
    cols = list(rets.columns)
    relations = []
    for follower in cols:
        best = None
        for leader in cols:
            if leader == follower:
                continue
            for lag in range(1, max_lag + 1):
                a = rets[leader].shift(lag)
                d = pd.concat([a, rets[follower]], axis=1).dropna()
                if len(d) < min_overlap:
                    continue
                c = d.iloc[:, 0].corr(d.iloc[:, 1])
                if c is not None and not np.isnan(c):
                    if best is None or c > best["corr"]:
                        best = dict(leader=leader, follower=follower, lag=lag, corr=float(c))
        if best and best["corr"] >= min_corr:
            # recent leader move over its lead window (is the wing flapping now?)
            lead_ret = float(np.expm1(rets[best["leader"]].tail(best["lag"]).sum()))
            best["leader_recent_pct"] = lead_ret * 100
            best["active"] = abs(lead_ret) >= 0.02      # >=2% recent leader move
            relations.append(best)
    relations.sort(key=lambda x: -x["corr"])
    return relations[:top]
