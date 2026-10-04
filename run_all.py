"""One command for the whole study.

    python run_all.py                  # in-sample: pipeline + backtests + every variant (results/is_*)
    python run_all.py --oos            # the single out-of-sample evaluation (results/oos_*), locked spec
    python run_all.py --from-signals   # skip data stages; backtest from the committed data/signals*.csv
                                       # (needs only Webull keys)

Data stages are cached and idempotent. Databento is never called beyond the caps in config.py; if the
priced pass would exceed them, the run stops and says what it would cost.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
from analysis import capacity, deflated_sharpe, factor_regression, metrics, plot_equity
from backtest import Params, load_bars, run
from signals import SIGNAL_COLUMNS, build_news, make_signals

R = config.RESULTS_DIR
R.mkdir(exist_ok=True)
VARIANT_LOG = R / "variants_log.csv"
OOS_LOCK = R / "OOS_LOCK.json"


def log_variant(name: str, period: str, m: dict, spec: dict):
    """Append a tested configuration, once: reproducing a logged run does not count as a new trial."""
    if VARIANT_LOG.exists():
        seen = pd.read_csv(VARIANT_LOG)
        if ((seen.variant == name) & (seen.period == period)).any():
            return
    row = {"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "variant": name, "period": period,
           "spec": json.dumps(spec, sort_keys=True), **{k: m.get(k) for k in ("sharpe", "ann_return", "max_drawdown", "n_trades")}}
    pd.DataFrame([row]).to_csv(VARIANT_LOG, mode="a", header=not VARIANT_LOG.exists(), index=False)


def spec_hash() -> str:
    keys = [k for k in dir(config) if k.isupper() and k not in ("DATA_DIR", "CACHE_DIR", "RESULTS_DIR", "TRACK_DIR", "REPO_ROOT", "ENV_FILE")]
    blob = json.dumps({k: getattr(config, k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------- data stages
def data_stages(spend: float | None):
    py = sys.executable
    if not (config.DATA_DIR / "universe.csv").exists():
        subprocess.run([py, "universe.py"], check=True, cwd=config.TRACK_DIR)
    if not (config.DATA_DIR / "spike_days.csv").exists():
        subprocess.run([py, "screen.py"], check=True, cwd=config.TRACK_DIR)
    if not (config.DATA_DIR / "whale_days.csv").exists():
        args = [py, "whales.py"] + (["--max-spend", str(spend)] if spend else [])
        subprocess.run(args, check=True, cwd=config.TRACK_DIR)
    whales = pd.read_csv(config.DATA_DIR / "whale_days.csv")
    stock = pd.read_parquet(config.DATA_DIR / "stock_daily.parquet")
    sessions = pd.DatetimeIndex(sorted(pd.to_datetime(stock.date).unique()))
    news = build_news(whales, sessions)
    whales.to_csv(config.DATA_DIR / "whale_days.csv", index=False)
    return whales, news


def load_inputs(from_signals: bool):
    if from_signals:
        return pd.read_csv(config.DATA_DIR / "whale_days.csv"), pd.read_csv(config.DATA_DIR / "news.csv")
    return data_stages(None)


# ------------------------------------------------------------------------------------------- analysis helpers
def signed_forward_returns(sig: pd.DataFrame, bars: dict, horizons=(1, 3, 5, 10, 20)) -> pd.DataFrame:
    """Event study for P4: direction-signed return from the open of t+1 to the close of t+h, raw and minus XBI."""
    xbi = bars[config.HEDGE_SYMBOL]
    rows = []
    for r in sig.itertuples():
        b = bars.get(r.ticker)
        if b is None:
            continue
        idx = b.index.searchsorted(pd.Timestamp(r.date), side="right")
        if idx >= len(b):
            continue
        entry_day, entry = b.index[idx], b.open.iloc[idx]
        xi = xbi.index.searchsorted(entry_day)
        row = {"ticker": r.ticker, "date": r.date, "direction": r.direction, "news_status": r.news_status,
               "whale_premium": r.whale_premium}
        for h in horizons:
            j = idx + h - 1
            if j < len(b) and xi + h - 1 < len(xbi):
                raw = b.close.iloc[j] / entry - 1
                mkt = xbi.close.iloc[xi + h - 1] / xbi.open.iloc[xi] - 1
                row[f"r{h}"] = r.direction * raw
                row[f"x{h}"] = r.direction * (raw - mkt)
        rows.append(row)
    return pd.DataFrame(rows)


def mean_ci(x: pd.Series, n_boot=4000, seed=0):
    x = x.dropna().to_numpy()
    if len(x) < 5:
        return np.nan, np.nan, np.nan, len(x)
    rng = np.random.default_rng(seed)
    b = rng.choice(x, (n_boot, len(x))).mean(1)
    return x.mean(), *np.percentile(b, [2.5, 97.5]), len(x)


def fmt_pct(v, nd=2):
    return "" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v * 100:+.{nd}f}%"


def metrics_table(rows: dict[str, dict]) -> pd.DataFrame:
    cols = ["ann_return", "ann_vol", "sharpe", "max_drawdown", "turnover_x", "worst_month", "skew", "n_trades",
            "hit_rate", "avg_trade_gross", "signals", "skipped", "missing_in_webull"]
    t = pd.DataFrame({k: {c: v.get(c) for c in cols} for k, v in rows.items()}).T
    return t


# ------------------------------------------------------------------------------------------- periods
def evaluate_period(label: str, start: str, end: str, whales: pd.DataFrame, news: pd.DataFrame, variants: bool):
    base_sig = make_signals(whales, news)
    base_sig[SIGNAL_COLUMNS].to_csv(config.DATA_DIR / "signals.csv", index=False)
    tickers = make_signals(whales, news, spike_mult=min(config.GRID["SPIKE_MULT"]),
                           min_premium=min(config.GRID["WHALE_MIN_PREMIUM"]), one_sided=min(config.GRID["ONE_SIDED_MIN"]),
                           news_rule="ignore")
    period_t = tickers[(tickers.date >= start) & (tickers.date <= end)].ticker.unique().tolist()
    bars, missing = load_bars(period_t + ["SPY"], start, end)
    print(f"[{label}] Webull bars for {len(bars)} symbols; {len(missing)} not on Webull (delisted/acquired): "
          f"{', '.join(sorted(missing)[:20])}{' …' if len(missing) > 20 else ''}")

    runs = {}

    def go(name, sig, P=None, spec=None):
        res = run(sig, start, end, P or Params(), bars=bars)
        m = metrics(res)
        runs[name] = (res, m)
        log_variant(name, label, m, spec or {})
        print(f"  [{label}] {name:44s} Sharpe {m.get('sharpe', float('nan')):+.2f}  ann {fmt_pct(m.get('ann_return'))}  "
              f"maxDD {fmt_pct(m.get('max_drawdown'))}  trades {m.get('n_trades', 0)}")
        return res, m

    go("baseline", base_sig)
    go("baseline ×2 costs", base_sig, Params(cost_mult=2.0), {"cost_mult": 2})
    go("no XBI hedge", base_sig, Params(hedge=False), {"hedge": False})
    go("no options tail hedge", base_sig, Params(tail_hedge=False), {"tail_hedge": False})
    go("no hedges at all", base_sig, Params(hedge=False, tail_hedge=False), {"hedge": False, "tail_hedge": False})
    go("no news filter (trade every whale day)", make_signals(whales, news, news_rule="ignore"), spec={"news_rule": "ignore"})
    go("P2 shadow: only days the news already covered", make_signals(whales, news, news_rule="covered_same_only"),
       spec={"news_rule": "covered_same_only"})
    if variants:
        go("news: trade only uncovered days", make_signals(whales, news, news_rule="uncovered_only"), spec={"news_rule": "uncovered_only"})
        for k, vals in config.GRID.items():
            for v in vals:
                if v == getattr(config, k):
                    continue
                if k == "HOLD_SESSIONS":
                    go(f"{k}={v}", base_sig, Params(hold=v), {k: v})
                else:
                    kw = {"SPIKE_MULT": "spike_mult", "WHALE_MIN_PREMIUM": "min_premium", "ONE_SIDED_MIN": "one_sided"}[k]
                    go(f"{k}={v}", make_signals(whales, news, **{kw: v}), spec={k: v})

    # ---- outputs
    base_res, base_m = runs["baseline"]
    xbi = bars[config.HEDGE_SYMBOL].close
    spy = bars["SPY"].close if "SPY" in bars else None
    reg = factor_regression(base_res["equity"], {"XBI": xbi, **({"SPY": spy} if spy is not None else {})})
    n_trials = len(pd.read_csv(VARIANT_LOG).query("period == @label")) if VARIANT_LOG.exists() else len(runs)
    sharpes = [m.get("sharpe") for _, m in runs.values() if m.get("sharpe") is not None and np.isfinite(m.get("sharpe"))]
    dsr = deflated_sharpe(base_m.get("sharpe", np.nan), base_m.get("days", 0), max(n_trials, 1), base_m.get("skew", 0.0),
                          base_m.get("excess_kurtosis", 0.0), float(np.std(sharpes)) if len(sharpes) > 2 else None)
    cap = capacity(base_res)
    sig_p = base_sig[(base_sig.date >= start) & (base_sig.date <= end)]
    ev = signed_forward_returns(make_signals(whales, news, news_rule="ignore").query("@start <= date <= @end"), bars)
    decay = []
    for status_group, sub in [("traded (not covered_same)", ev[ev.news_status != "covered_same"]),
                              ("covered_same (skipped)", ev[ev.news_status == "covered_same"]), ("all whale days", ev)]:
        for h in (1, 3, 5, 10, 20):
            for col, lab in ((f"r{h}", "raw"), (f"x{h}", "minus XBI")):
                if col in sub:
                    m_, lo, hi, n = mean_ci(sub[col])
                    decay.append({"group": status_group, "horizon": h, "measure": lab, "mean": m_, "ci_lo": lo, "ci_hi": hi, "n": n})
    decay = pd.DataFrame(decay)
    terc = pd.DataFrame()
    t = base_res["trades"]
    if len(t) >= 9:
        t = t.assign(tercile=pd.qcut(t.whale_premium.rank(method="first"), 3, labels=["small", "mid", "large"]))
        terc = t.groupby("tercile", observed=True).gross_return.agg(["mean", "count"])

    mt = metrics_table({k: m for k, (_, m) in runs.items()})
    mt.to_csv(R / f"{label}_metrics.csv")
    base_res["trades"].to_csv(R / f"{label}_trades.csv", index=False)
    base_res["skipped"].to_csv(R / f"{label}_skipped.csv", index=False)
    base_res["hedge_notes"].to_csv(R / f"{label}_tail_hedges.csv", index=False)
    base_res["equity"].rename("equity").to_csv(R / f"{label}_equity.csv")
    decay.to_csv(R / f"{label}_decay.csv", index=False)
    cap.to_csv(R / f"{label}_capacity.csv", index=False)
    plot_equity({label_name(label): base_res, f"{label_name(label)} ×2 costs": runs["baseline ×2 costs"][0],
                 "XBI": (xbi[(xbi.index >= base_res["equity"].index[0]) & (xbi.index <= base_res["equity"].index[-1])]
                         if len(base_res["equity"]) else xbi)},
                R / f"{label}_equity.png", f"Whale footprints · {label_name(label)} {start}..{end} · net of costs")
    summary = {
        "period": label, "start": start, "end": end, "spec_hash": spec_hash(),
        "baseline": base_m, "baseline_x2_costs": runs["baseline ×2 costs"][1], "no_hedge": runs["no XBI hedge"][1],
        "no_tail_hedge": runs["no options tail hedge"][1], "no_hedges": runs["no hedges at all"][1],
        "tail_hedge_coverage": (base_res["hedge_notes"].hedged.mean() if len(base_res["hedge_notes"]) else None),
        "tail_hedge_median_premium_pct": (base_res["hedge_notes"].premium_pct_of_position.median() if len(base_res["hedge_notes"]) else None),
        "worst_trades_with_vs_without_hedge": (base_res["trades"].nsmallest(5, "gross_return")[["ticker", "entry_date", "gross_return", "return_with_hedge"]]
                                               .astype(str).to_dict("records") if len(base_res["trades"]) else []),
        "factor_regression": reg, "n_trials_this_period": n_trials, "deflated_sharpe_prob": dsr,
        "signals_in_period": len(sig_p), "news_status_counts": sig_p.news_status.value_counts().to_dict(),
        "webull_missing_symbols": sorted(missing),
        "tercile_mean_trade_return": terc["mean"].to_dict() if len(terc) else {},
    }
    (R / f"{label}_summary.json").write_text(json.dumps(summary, indent=2, default=lambda o: o if not isinstance(o, float) else round(o, 6)))
    print(f"\n[{label}] metrics (baseline and variants):")
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.float_format", "{:.4f}".format):
        print(mt)
    print(f"\n[{label}] factor regression: {reg}\n[{label}] deflated Sharpe probability over {n_trials} trials: {dsr}")
    print(f"[{label}] decay (direction-signed, mean [95% CI]):")
    print(decay.pivot_table(index=["group", "measure"], columns="horizon", values="mean").map(fmt_pct).to_string())
    return summary


def label_name(label: str) -> str:
    return {"is": "in-sample", "oos": "out-of-sample"}[label]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos", action="store_true", help="run the locked out-of-sample evaluation")
    ap.add_argument("--from-signals", action="store_true", help="skip data stages (needs only Webull keys)")
    ap.add_argument("--no-variants", action="store_true")
    args = ap.parse_args()
    whales, news = load_inputs(args.from_signals)
    if not args.oos:
        evaluate_period("is", config.IS_START, config.IS_END, whales, news, variants=not args.no_variants)
        return
    h = spec_hash()
    if OOS_LOCK.exists():
        lock = json.loads(OOS_LOCK.read_text())
        if lock["spec_hash"] != h:
            sys.exit(f"Out-of-sample was already evaluated on {lock['first_run']} with spec {lock['spec_hash']}; "
                     f"the spec has changed since ({h}). Re-running it on a new spec would be tuning on the test set.")
        print(f"Reproducing the locked out-of-sample run first made {lock['first_run']} (same spec {h}).")
    else:
        OOS_LOCK.write_text(json.dumps({"first_run": datetime.now(timezone.utc).isoformat(timespec="seconds"), "spec_hash": h}))
    evaluate_period("oos", config.OOS_START, config.OOS_END, whales, news, variants=False)


if __name__ == "__main__":
    main()
