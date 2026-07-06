import numpy as np, pandas as pd
from engine import analyze

np.random.seed(7)
N = 300
def make(kind):
    x = np.arange(N)
    if kind=='clean_up':   base = 100*np.exp(0.0012*x) ; noise=0.006
    if kind=='clean_down': base = 100*np.exp(-0.0012*x); noise=0.006
    if kind=='choppy':     base = 100 + 8*np.sin(x/12) ; noise=0.02   # sideways, no trend
    if kind=='messy_up':   base = 100*np.exp(0.0012*x) ; noise=0.035  # up but violent
    close = base*(1+np.random.normal(0,noise,N)).cumprod()**0  # keep base shape
    close = base*(1+np.random.normal(0,noise,N))
    close = pd.Series(close).clip(lower=1)
    high = close*(1+abs(np.random.normal(0,noise,N)))
    low  = close*(1-abs(np.random.normal(0,noise,N)))
    return pd.DataFrame({'High':high,'Low':low,'Close':close})

for k in ['clean_up','messy_up','choppy','clean_down']:
    r = analyze(make(k))
    print(f"{k:11s} dir={r['direction']:4s} mom={r['mom']:+7.3f} r2={r['r2']:.2f} "
          f"tmpl={r['template']:.2f} adx={r['adx']:5.1f} rsi={r['rsi']:4.1f}")
