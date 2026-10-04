"""Performance, risk and capacity analysis of a backtest result (backtest.run output)."""
from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

import config

TRADING_DAYS = 252


def metrics(res: dict) -> dict:
    eq = res["equity"]
    if len(eq) < 3:
        return {"days": len(eq)}
    r = eq.pct_change().dropna()
    years = len(r) / TRADING_DAYS
    total = eq.iloc[-1] / eq.iloc[0] - 1
    ann_ret = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std(ddof=1) * math.sqrt(TRADING_DAYS)
    sharpe = r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS) if r.std() > 0 else np.nan
    dd = eq / eq.cummax() - 1
    traded = res["traded"]
    turnover = traded["notional"].sum() / eq.mean() / years if years > 0 and len(traded) else 0.0
    monthly = eq.resample("ME").last().pct_change().dropna()
    t = res["trades"]
    out = {"start": eq.index[0].date().isoformat(), "end": eq.index[-1].date().isoformat(), "days": len(r),
           "ann_return": ann_ret, "ann_vol": vol, "sharpe": sharpe, "max_drawdown": dd.min(),
           "turnover_x": turnover, "worst_month": monthly.min() if len(monthly) else np.nan,
           "skew": float(stats.skew(r)), "excess_kurtosis": float(stats.kurtosis(r)),
           "total_return": total, "n_trades": len(t), "signals": res["n_signals"],
           "skipped": len(res["skipped"]), "missing_in_webull": len(res["missing"])}
    if len(t):
        out.update(hit_rate=float((t.gross_return > 0).mean()), avg_trade_gross=float(t.gross_return.mean()),
                   stops=int((t.exit_reason == "stop").sum()),
                   median_adv_participation=float(t.adv_participation.median()) if "adv_participation" in t else np.nan)
    return out


def deflated_sharpe(sr_ann: float, n_obs: int, n_trials: int, skew: float, kurt_excess: float,
                    trial_sr_std_ann: float | None = None) -> float:
    """Bailey & López de Prado (2014): probability the true Sharpe > the best of n_trials lucky ones."""
    if not (n_obs > 1 and n_trials >= 1 and np.isfinite(sr_ann)):
        return np.nan
    sr = sr_ann / math.sqrt(TRADING_DAYS)
    sd = (trial_sr_std_ann / math.sqrt(TRADING_DAYS)) if trial_sr_std_ann else 1 / math.sqrt(n_obs)
    g = 0.5772156649
    z = stats.norm.ppf
    sr0 = sd * ((1 - g) * z(1 - 1 / n_trials) + g * z(1 - 1 / (n_trials * math.e))) if n_trials > 1 else 0.0
    denom = math.sqrt(max(1 - skew * sr + (kurt_excess + 2) / 4 * sr ** 2, 1e-12))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / denom))


def factor_regression(eq: pd.Series, factors: dict[str, pd.Series]) -> dict:
    """Daily strategy returns on factor returns (OLS with an intercept); alpha annualised."""
    r = eq.pct_change().dropna().rename("strat")
    X = pd.concat({k: v.pct_change() for k, v in factors.items()}, axis=1)
    df = pd.concat([r, X], axis=1, join="inner").dropna()
    if len(df) < 30:
        return {}
    A = np.column_stack([np.ones(len(df))] + [df[k].to_numpy() for k in factors])
    y = df["strat"].to_numpy()
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    s2 = resid @ resid / (len(y) - A.shape[1])
    se = np.sqrt(np.diag(s2 * np.linalg.inv(A.T @ A)))
    out = {"alpha_ann": beta[0] * TRADING_DAYS, "alpha_t": beta[0] / se[0],
           "r2": 1 - resid @ resid / ((y - y.mean()) @ (y - y.mean()))}
    for i, k in enumerate(factors, 1):
        out[f"beta_{k}"], out[f"t_{k}"] = beta[i], beta[i] / se[i]
    return out


def capacity(res: dict, aums=(1e6, 5e6, 25e6, 100e6), impact_k: float = 1.0) -> pd.DataFrame:
    """Square-root impact on the trades actually taken, scaled to larger books.

    At AUM A each position scales by A / cash (until the ADV cap binds); impact cost per side
    ≈ k · σ_daily · sqrt(shares / ADV). Returns the impact drag on the book and net return per trade.
    """
    t = res["trades"]
    if t.empty:
        return pd.DataFrame()
    base = res["params"].cash
    rows = []
    for aum in aums:
        scale = aum / base
        shares = np.minimum(t.shares * scale, config.MAX_ADV_FRACTION * 5 * t.adv_shares)   # allow up to 10% ADV
        part = shares / t.adv_shares
        impact = impact_k * t.sigma20 * np.sqrt(part) * 2          # in and out
        notional = shares * t.entry_price
        rows.append({"aum": aum, "median_participation": float(part.median()),
                     "mean_impact_bps_round_trip": float(impact.mean() * 1e4),
                     "net_mean_trade_return_after_impact": float((t.gross_return - impact - 2 * config.COST_BPS_STOCK / 1e4).mean()),
                     "capped_trades": int((t.shares * scale > config.MAX_ADV_FRACTION * 5 * t.adv_shares).sum()),
                     "deployed_notional_median": float(notional.median())})
    return pd.DataFrame(rows)


def plot_equity(results: dict[str, dict], path, title: str):
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    colors = {"in-sample": "#2a78d6", "out-of-sample": "#1baf7a", "in-sample ×2 costs": "#8fb8ea",
              "out-of-sample ×2 costs": "#93d9bd", "XBI": "#898781"}
    for name, res in results.items():
        eq = res["equity"] if isinstance(res, dict) else res
        if len(eq) < 2:
            continue
        g = eq / eq.iloc[0]
        axes[0].plot(g.index, g.values, label=name, color=colors.get(name), linewidth=1.8 if "×2" not in name else 1.1,
                     linestyle="--" if "×2" in name or name == "XBI" else "-")
        if name in ("in-sample", "out-of-sample"):
            axes[1].fill_between(g.index, (g / g.cummax() - 1).values * 100, 0, color=colors.get(name), alpha=0.35)
    axes[0].axvline(pd.Timestamp(config.OOS_START), color="#c3c2b7", linestyle=":", linewidth=1)
    axes[0].set_ylabel("growth of $1")
    axes[0].set_title(title, loc="left", fontweight="bold")
    axes[0].legend(frameon=False, fontsize=8)
    axes[1].set_ylabel("drawdown %")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e1e0d9", linewidth=0.6)
    plt.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
