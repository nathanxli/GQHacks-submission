"""Generate the figure pack for QUANT_NOTE.md from committed result files.

Figures (saved to tracks/whale_footprints/figures/):
  fig1_equity.png       IS & OOS locked-plan equity curves with drawdown shading
  fig2_decay.png        direction-signed return by horizon, traded vs news-covered days, IS & OOS (P2, P4)
  fig5_pnl_source.png   mean trade return by direction, and with the five best trades removed
  fig3_sensitivity.png  in-sample Sharpe of every configuration run (the full trial set)
  fig4_hedge.png        tail-hedge premium distribution, and the worst trades gross vs hedged
  figA2_capacity.png     net mean trade return after square-root impact vs AUM, IS & OOS
  figA1_funnel.png       signal funnel from universe to completed trades (appendix)

Run after run_all.py:  python make_figures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

TRACK = Path(__file__).resolve().parent
RES = TRACK / "results"
DATA = TRACK / "data"
FIG = TRACK / "figures"
FIG.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.size": 10, "axes.titlesize": 10.5, "axes.labelsize": 10,
    "legend.fontsize": 8.5, "figure.dpi": 150, "savefig.dpi": 200,
    "axes.grid": True, "grid.alpha": 0.3, "axes.spines.top": False,
    "axes.spines.right": False,
})
GREEN, RED, GREY, BLUE, ORANGE = "#1a7f37", "#c62828", "#8a8a8a", "#1565c0", "#e08a00"
PERIODS = {"is": "In-sample", "oos": "Out-of-sample"}


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name, bbox_inches="tight")
    plt.close(fig)
    print(name)


def boot_ci(x: pd.Series, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    a = x.to_numpy()
    m = rng.choice(a, size=(n, len(a))).mean(1)
    return a.mean(), np.percentile(m, 2.5), np.percentile(m, 97.5)


trades = {p: pd.read_csv(RES / f"{p}_trades.csv") for p in PERIODS}

# --------------------------------------------------------------------------- #
# fig 1 · locked-plan equity curves
# --------------------------------------------------------------------------- #
fig, axes = plt.subplots(1, 2, figsize=(9, 2.25), sharey=True, gridspec_kw={"width_ratios": [2.2, 1]})
for ax, p in zip(axes, PERIODS):
    eq = pd.read_csv(RES / f"{p}_equity.csv")
    eq.columns = ["date", "equity"]
    eq["date"] = pd.to_datetime(eq["date"])
    peak = eq.equity.cummax()
    ax.plot(eq.date, eq.equity / 1e6, color=BLUE, lw=1.2, label="locked plan, net of costs")
    ax.fill_between(eq.date, eq.equity / 1e6, peak / 1e6, where=eq.equity < peak, color=RED, alpha=0.18,
                    label="drawdown")
    ax.axhline(1.0, color=GREY, lw=0.8)
    ax.set_title(f"{PERIODS[p]} · ends ${eq.equity.iloc[-1]/1e6:.2f}M")
    ax.tick_params(axis="x", rotation=25)
axes[0].set_ylabel("equity ($M, start $1M)")
axes[0].legend(loc="lower left")
save(fig, "fig1_equity.png")

# --------------------------------------------------------------------------- #
# fig 2 · decay by horizon: traded days vs news-covered (skipped) days
# --------------------------------------------------------------------------- #
fig, axes = plt.subplots(1, 2, figsize=(9, 2.35), sharey=True)
for ax, p in zip(axes, PERIODS):
    dec = pd.read_csv(RES / f"{p}_decay.csv")
    dec = dec[dec.measure == "raw"]
    for grp, col, lab, off in [("traded (not covered_same)", BLUE, "traded (news silent or opposite)", -0.15),
                               ("covered_same (skipped)", ORANGE, "skipped (news already agreed)", 0.15)]:
        d = dec[dec.group == grp]
        x = d.horizon.astype(int) + off
        n = int(d[d.horizon == 5].n.iloc[0])
        ax.errorbar(x, d["mean"] * 100, yerr=[(d["mean"] - d.ci_lo) * 100, (d.ci_hi - d["mean"]) * 100],
                    fmt="o-", color=col, lw=1.3, ms=3.5, capsize=2, label=f"{lab}, n={n}")
    ax.axhline(0, color=GREY, lw=0.8)
    ax.axvline(5, color=GREY, lw=0.8, ls=":")
    ax.set_xticks([1, 3, 5, 10, 20])
    ax.set_ylim(-15, 10)
    ax.set_title(PERIODS[p] + (" (orange CIs clipped at −15%)" if p == "oos" else ""))
    ax.set_xlabel("sessions after the signal (dotted: our 5-session exit)")
axes[0].set_ylabel("direction-signed return (%)")
axes[0].legend(loc="upper left", fontsize=7.5)
save(fig, "fig2_decay.png")

# --------------------------------------------------------------------------- #
# fig 5 · where the gross P&L comes from: direction, and the five best trades
# --------------------------------------------------------------------------- #
fig, axes = plt.subplots(1, 2, figsize=(9, 2.3))
ax = axes[0]
for i, p in enumerate(PERIODS):
    t = trades[p]
    for j, (k, lab, col) in enumerate([(1, "long (bullish whales)", GREEN), (-1, "short (bearish whales)", RED)]):
        m, lo, hi = boot_ci(t[t.direction == k].gross_return)
        ax.bar(i + (j - 0.5) * 0.36, m * 100, 0.36, color=col, alpha=0.8, label=lab if i == 0 else None)
        ax.errorbar(i + (j - 0.5) * 0.36, m * 100, yerr=[[(m - lo) * 100], [(hi - m) * 100]], color="k", capsize=3, lw=1)
ax.axhline(0, color=GREY, lw=0.8)
ax.set_xticks([0, 1])
ax.set_xticklabels([f"{PERIODS[p]} (n={len(trades[p])})" for p in PERIODS])
ax.set_ylabel("mean gross trade return (%)")
ax.set_title("By direction (95% bootstrap CI)")
ax.legend(loc="upper left")
ax = axes[1]
labels = ["all trades", "without the 5 best", "trim 5 best + 5 worst"]
for i, p in enumerate(PERIODS):
    s = trades[p].gross_return.sort_values(ascending=False)
    vals = [s.mean(), s.iloc[5:].mean(), s.iloc[5:-5].mean()]
    for j, v in enumerate(vals):
        ax.bar(i + (j - 1) * 0.26, v * 100, 0.26, color=[BLUE, RED, GREY][j], alpha=0.85,
               label=labels[j] if i == 0 else None)
        ax.text(i + (j - 1) * 0.26, v * 100 + 0.06 if v >= 0 else 0.06, f"{v*100:+.2f}", ha="center", fontsize=7.5)
ax.axhline(0, color=GREY, lw=0.8)
ax.set_xticks([0, 1])
ax.set_xticklabels(list(PERIODS.values()))
ax.set_title("Concentration: the mean rests on a few trades")
ax.legend(loc="upper left")
save(fig, "fig5_pnl_source.png")

# --------------------------------------------------------------------------- #
# fig 3 · every in-sample configuration run (the full trial set)
# --------------------------------------------------------------------------- #
m = pd.read_csv(RES / "is_metrics.csv", index_col=0)
order = ["baseline", "baseline ×2 costs",
         "SPIKE_MULT=2.0", "SPIKE_MULT=4.0", "WHALE_MIN_PREMIUM=25000", "WHALE_MIN_PREMIUM=100000",
         "ONE_SIDED_MIN=0.55", "ONE_SIDED_MIN=0.75", "HOLD_SESSIONS=3", "HOLD_SESSIONS=10",
         "no news filter (trade every whale day)", "news: trade only uncovered days",
         "P2 shadow: only days the news already covered",
         "no XBI hedge", "no options tail hedge", "no hedges at all"]
nice = {"baseline": "LOCKED PLAN (3×, $50k, 65%, 5d)", "baseline ×2 costs": "locked plan, ×2 costs",
        "SPIKE_MULT=2.0": "spike 2×", "SPIKE_MULT=4.0": "spike 4×",
        "WHALE_MIN_PREMIUM=25000": "whale print ≥ $25k", "WHALE_MIN_PREMIUM=100000": "whale print ≥ $100k",
        "ONE_SIDED_MIN=0.55": "one-sided ≥ 55%", "ONE_SIDED_MIN=0.75": "one-sided ≥ 75%",
        "HOLD_SESSIONS=3": "hold 3 sessions", "HOLD_SESSIONS=10": "hold 10 sessions",
        "no news filter (trade every whale day)": "news: ignore", "news: trade only uncovered days": "news: only uncovered",
        "P2 shadow: only days the news already covered": "news: only covered (shadow)",
        "no XBI hedge": "no XBI hedge", "no options tail hedge": "no options tail hedge",
        "no hedges at all": "no hedges at all"}
group_col = [BLUE, BLUE] + [GREY] * 8 + [ORANGE] * 3 + [GREEN] * 3
fig, ax = plt.subplots(figsize=(9, 2.15))
y = np.arange(len(order))[::-1]
sh = m.loc[order, "sharpe"].to_numpy()
ax.barh(y, sh, color=group_col, alpha=0.85)
for yi, v in zip(y, sh):
    ax.text(v + (0.02 if v >= 0 else -0.02), yi, f"{v:+.2f}", va="center", ha="left" if v >= 0 else "right", fontsize=7.5)
ax.set_yticks(y)
ax.set_yticklabels([nice[o] for o in order], fontsize=7.5)
ax.axvline(0, color="k", lw=0.8)
ax.set_xlim(-1.35, 0.35)
ax.set_xlabel("in-sample Sharpe, net of costs (blue: locked plan · grey: neighbouring parameters · "
              "orange: news rule · green: hedges)", fontsize=8.5)
save(fig, "fig3_sensitivity.png")

# --------------------------------------------------------------------------- #
# fig 4 · the tail hedge: what it cost, and what it saved
# --------------------------------------------------------------------------- #
th = pd.concat([pd.read_csv(RES / f"{p}_tail_hedges.csv") for p in PERIODS]).dropna(subset=["premium_pct_of_position"])
th = th[th.hedged]
fig, axes = plt.subplots(1, 2, figsize=(9, 2.3), gridspec_kw={"width_ratios": [1.2, 1]})
ax = axes[0]
pct = th.premium_pct_of_position * 100
ax.hist(pct.clip(upper=40), bins=40, color=BLUE, alpha=0.75)
allt = pd.concat([trades[p].assign(period=p) for p in PERIODS])
h = allt[allt.hedged]
ax.axvline(pct.median(), color=RED, lw=1.4, ls="--", label=f"median premium paid up front: {pct.median():.1f}%")
ax.axvline(-h.hedge_pnl_pct.median() * 100, color=ORANGE, lw=1.4,
           label=f"median net cost after resale at exit: {-h.hedge_pnl_pct.median()*100:.1f}%")
ax.axvline(allt.gross_return.mean() * 100, color=GREEN, lw=1.4,
           label=f"mean gross trade return: {allt.gross_return.mean()*100:.1f}%")
ax.legend(loc="upper right", fontsize=7.5)
ax.set_xlabel("hedge premium, % of position (IS + OOS, clipped at 40%)")
ax.set_ylabel("hedged positions")
ax.set_title(f"Cost: paid on every hedged position (n={len(th)})")
ax = axes[1]
worst = allt.nsmallest(6, "gross_return")
xx = np.arange(len(worst))
ax.bar(xx - 0.18, worst.gross_return * 100, 0.36, color=RED, label="gross (no hedge)")
ax.bar(xx + 0.18, worst.return_with_hedge * 100, 0.36, color=GREEN, label="with tail hedge")
ax.set_xticks(xx)
ax.set_xticklabels([f"{r.ticker}\n{r.period.upper()}{'' if r.hedged else '*'}" for r in worst.itertuples()],
                   fontsize=7.5)
ax.set_ylabel("trade return (%)")
ax.set_title("Benefit: the six worst trades (* no hedge contract traded)")
ax.legend(loc="lower right", fontsize=7.5)
save(fig, "fig4_hedge.png")

# --------------------------------------------------------------------------- #
# appendix · capacity, IS and OOS
# --------------------------------------------------------------------------- #
fig, ax = plt.subplots(figsize=(9, 2.6))
for p, col in [("is", BLUE), ("oos", ORANGE)]:
    cap = pd.read_csv(RES / f"{p}_capacity.csv")
    ax.plot(cap.aum, cap.net_mean_trade_return_after_impact * 100, "o-", color=col, lw=1.4,
            label=f"{PERIODS[p]} (gross edge {trades[p].gross_return.mean()*100:.2f}% per trade)")
    for a, v in zip(cap.aum, cap.net_mean_trade_return_after_impact * 100):
        ax.text(a, v + 0.15, f"{v:+.2f}", ha="center", fontsize=7.5, color=col)
ax.axhline(0, color=GREY, lw=0.8)
ax.set_xscale("log")
ax.set_xticks(cap.aum)
ax.set_xticklabels([f"${v/1e6:.0f}M" for v in cap.aum])
ax.minorticks_off()
ax.set_xlabel("strategy AUM")
ax.set_ylabel("net trade return (%)")
ax.set_title("Mean trade return after 40 bps round-trip fees and square-root impact")
ax.legend(loc="lower left")
save(fig, "figA2_capacity.png")

# --------------------------------------------------------------------------- #
# appendix · signal funnel
# --------------------------------------------------------------------------- #
sig = pd.read_csv(DATA / "signals.csv")
stats = json.loads((RES / "note_stats.json").read_text())
steps = [("biotech names with listed options", int(pd.read_csv(DATA / "universe.csv").any_listed.sum())),
         ("Massive volume spikes ≥ 2× (name-days)", len(pd.read_csv(DATA / "spike_days.csv"))),
         ("stage-1 pool (floor, one-sided, liquid)", stats["funnel"]["stage1_pool"] or 0),
         ("signed on Databento (seeded 30%)", len(pd.read_csv(DATA / "whale_days.csv"))),
         ("whale days (3×, ≥1,000, ≥65% one-sided)", len(sig)),
         ("not already covered by same-direction news", int(sig.trade.sum())),
         ("completed backtest trades (IS + OOS)", sum(len(t) for t in trades.values()))]
fig, ax = plt.subplots(figsize=(9, 2.6))
steps = [s for s in steps if s[1]]
y = np.arange(len(steps))[::-1]
ax.barh(y, [s[1] for s in steps], color=BLUE, alpha=0.8)
ax.set_xscale("log")
for yi, (_, v) in zip(y, steps):
    ax.text(v * 1.1, yi, f"{v:,}", va="center", fontsize=8)
ax.set_yticks(y)
ax.set_yticklabels([s[0] for s in steps], fontsize=8.5)
ax.set_xlim(100, 4e5)
ax.set_xlabel("count (log scale)")
ax.grid(axis="y", visible=False)
save(fig, "figA1_funnel.png")
print(f"\nFigures written to {FIG}")
