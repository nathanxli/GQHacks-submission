"""Post-hoc diagnostics quoted in QUANT_NOTE.md, computed from committed result files only.

None of these changed the strategy; they describe the locked-plan runs. Writes results/note_stats.json.
The spread estimate, survivorship check and stage-1 funnel count need data/stock_daily.parquet (raw Massive bars,
gitignored; rebuilt by `python run_all.py`). Without it, the committed values in note_stats.json are kept.
Run after run_all.py:  python note_stats.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

TRACK = Path(__file__).resolve().parent
RES = TRACK / "results"
DATA = TRACK / "data"
WINDOWS = {"is": ("2024-01-02", "2026-03-13"), "oos": ("2026-03-16", "2026-10-02")}


def spread_estimate(trades: pd.DataFrame) -> dict:
    """Abdi-Ranaldo (2017) close-high-low spread, pooled over the 20 sessions before each signal.

    Needs data/stock_daily.parquet (Massive grouped daily bars, gitignored); skipped if absent."""
    path = DATA / "stock_daily.parquet"
    if not path.exists():
        return {"available": False}
    d = pd.read_parquet(path).sort_values(["ticker", "date"])
    d["date"] = pd.to_datetime(d["date"])
    eta = (np.log(d.high) + np.log(d.low)) / 2
    d["s2"] = 4 * (np.log(d.close) - eta) * (np.log(d.close) - eta.groupby(d.ticker).shift(-1))
    d["dv"] = d.close * d.volume
    by = {t: g for t, g in d.groupby("ticker")}
    rows = []
    for _, r in trades.iterrows():
        g = by.get(r.ticker)
        if g is None:
            continue
        x = g[g.date < r.signal_date].tail(21)
        rows.append((x.s2.mean(), x.dv.median()))
    a = pd.DataFrame(rows, columns=["s2", "dv"]).dropna()
    a["tercile"] = pd.qcut(a.dv, 3, labels=["low", "mid", "high"])
    terc = a.groupby("tercile", observed=True).s2.mean()
    bps = lambda s2: float(np.sqrt(s2) * 1e4) if s2 > 0 else None   # negative mean = estimator undefined
    return {"available": True, "pooled_full_spread_bps": bps(a.s2.mean()),
            "by_adv_tercile_full_spread_bps": {k: bps(v) for k, v in terc.items()}}


def survivorship(p: str, start: str, end: str) -> dict:
    """Direction-signed 5-session open-to-open return of traded signals on names Webull no longer carries,
    priced on Massive bars (which keep delisted names). Needs data/stock_daily.parquet."""
    path = DATA / "stock_daily.parquet"
    if not path.exists():
        return {"available": False}
    st = pd.read_parquet(path)
    st["date"] = pd.to_datetime(st["date"])
    miss = set(json.loads((RES / f"{p}_summary.json").read_text())["webull_missing_symbols"])
    s = pd.read_csv(DATA / "signals.csv", parse_dates=["date"])
    s = s[s.trade & (s.date >= start) & (s.date <= end) & s.ticker.isin(miss)]
    rets = []
    for r in s.itertuples():
        g = st[(st.ticker == r.ticker) & (st.date > r.date)].sort_values("date")
        if len(g) >= 6:
            rets.append(r.direction * (g.open.iloc[5] / g.open.iloc[0] - 1))
    return {"available": True, "signals": int(len(s)), "names": int(s.ticker.nunique()), "priced": len(rets),
            "mean_signed_5d": float(np.mean(rets)) if rets else None}


def period_stats(p: str) -> dict:
    t = pd.read_csv(RES / f"{p}_trades.csv", parse_dates=["signal_date", "entry_date", "exit_date"])
    eq = pd.read_csv(RES / f"{p}_equity.csv", index_col=0, parse_dates=True)["equity"]
    th = pd.read_csv(RES / f"{p}_tail_hedges.csv")
    s = t.sort_values("gross_return", ascending=False)
    r = eq.pct_change().dropna()
    dd = eq / eq.cummax() - 1

    # Concurrent stock exposure from the trade list (entry-to-exit, weight at entry).
    days = eq.index
    gross = pd.Series(0.0, index=days)
    net = pd.Series(0.0, index=days)
    for _, x in t.iterrows():
        m = (days >= x.entry_date) & (days < x.exit_date)
        gross[m] += x.weight
        net[m] += x.weight * x.direction
    active = gross[gross > 0]

    worst = t.loc[t.gross_return.idxmin()]
    return {
        "n_trades": int(len(t)),
        "by_direction": {("long" if k == 1 else "short"): {"n": int(len(g)), "mean_gross": float(g.gross_return.mean()),
                                                         "mean_with_tail_hedge": float(g.return_with_hedge.mean()),
                                                         "hit_rate": float((g.gross_return > 0).mean())}
                         for k, g in t.groupby("direction")},
        "mean_gross": float(t.gross_return.mean()),
        "median_gross": float(t.gross_return.median()),
        "mean_with_tail_hedge": float(t.return_with_hedge.mean()),
        "top5_share_of_gross_sum": float(s.gross_return.head(5).sum() / t.gross_return.sum()),
        "mean_gross_ex_top5": float(s.gross_return.iloc[5:].mean()),
        "mean_gross_trim5_each_side": float(s.gross_return.iloc[5:-5].mean()),
        "calendar_year": {int(y): {"return": float((1 + g).prod() - 1),
                                   "sharpe": float(g.mean() / g.std() * np.sqrt(252)), "sessions": int(len(g))}
                          for y, g in r.groupby(r.index.year)},
        "share_of_sessions_derisked": float((dd < -0.15).mean()),
        "median_weight": float(t.weight.median()), "max_weight": float(t.weight.max()),
        "adv_participation": {"median": float(t.adv_participation.median()),
                              "p95": float(t.adv_participation.quantile(0.95)), "max": float(t.adv_participation.max())},
        "stock_gross_exposure_when_invested": {"median": float(active.median()), "max": float(active.max())},
        "stock_net_exposure_before_xbi": {"median": float(net[gross > 0].median()),
                                          "min": float(net.min()), "max": float(net.max())},
        "worst_trade": {"ticker": worst.ticker, "gross_return": float(worst.gross_return),
                        "weight": float(worst.weight), "equity_hit_gross": float(worst.gross_return * worst.weight),
                        "equity_hit_with_hedge": float(worst.return_with_hedge * worst.weight)},
        "tail_hedge": {"coverage": float(th.hedged.mean()),
                       "premium_pct_quantiles": {str(q): float(v) for q, v in
                                                 th.premium_pct_of_position.quantile([.25, .5, .75, .9]).items()},
                       "unhedged_reasons": th.reason.dropna().value_counts().to_dict()},
        "spread": spread_estimate(t),
        "survivorship": survivorship(p, *WINDOWS[p]),
    }


if __name__ == "__main__":
    out = {p: period_stats(p) for p in ["is", "oos"]}
    v = pd.read_csv(RES / "variants_log.csv")
    if (DATA / "stock_daily.parquet").exists():
        import whales
        out["funnel"] = {"stage1_pool": int(len(whales.candidates(return_pool=True)))}
    else:
        out["funnel"] = {"stage1_pool": None}
    out["backtests_run"] = {"in_sample": int((v.period == "is").sum()), "out_of_sample": int((v.period == "oos").sum()),
                            "total": int(len(v))}
    # Without the raw Massive bars, keep the committed values of the three stats that need them.
    prev_path = RES / "note_stats.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text())
        for p in ["is", "oos"]:
            for k in ["spread", "survivorship"]:
                if not out[p][k].get("available") and prev.get(p, {}).get(k, {}).get("available"):
                    out[p][k] = prev[p][k]
        if out["funnel"]["stage1_pool"] is None:
            out["funnel"] = prev.get("funnel", out["funnel"])
    (RES / "note_stats.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
