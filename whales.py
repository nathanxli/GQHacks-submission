"""Stage 2 · whale prints on Databento OPRA (tcbbo: every option trade with the consolidated best bid/offer).

For each spike day from stage 1, one tcbbo request for the whole chain of that name over regular hours.
Each trade is signed by where it printed against the top of book (at/above ask = bought, at/below bid =
sold, otherwise the nearer side). Whale prints are trades with premium ≥ a threshold; bullish premium is
calls bought + puts sold, bearish is puts bought + calls sold.

Spend: every day is priced first (free), the total is shown, and nothing is bought if it would exceed the
study or run cap in config.py. Fetched days are cached, so reruns cost $0.

Output: data/whale_days.csv (one row per name-day, features for every grid threshold).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import config
from sources import BudgetExceeded, Databento

ET = "America/New_York"


def rth_window(day: str) -> tuple[str, str]:
    d = pd.Timestamp(day)
    start = pd.Timestamp(f"{d:%Y-%m-%d} 09:30", tz=ET).tz_convert("UTC")
    end = pd.Timestamp(f"{d:%Y-%m-%d} 16:15", tz=ET).tz_convert("UTC")
    return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")


def parse_occ(sym: pd.Series) -> pd.DataFrame:
    """'SRPT  250620C00100000' -> kind, strike, expiry."""
    s = sym.astype(str).str.replace(" ", "", regex=False)
    tail = s.str[-15:]
    return pd.DataFrame({"kind": np.where(tail.str[6] == "C", "call", "put"),
                         "strike": tail.str[7:].astype(float) / 1000,
                         "expiry": pd.to_datetime(tail.str[:6], format="%y%m%d")}, index=sym.index)


def day_features(trades: pd.DataFrame) -> dict:
    """Signed premium totals for one name-day."""
    if trades.empty:
        return {"contracts": 0, "premium": 0.0}
    t = trades[trades["size"] > 0].reset_index(drop=True)      # Databento timestamps repeat; use positions
    t = pd.concat([t, parse_occ(t["symbol"])], axis=1)
    bid, ask, px = t["bid_px_00"], t["ask_px_00"], t["price"]
    mid = (bid + ask) / 2
    quoted = (bid > 0) & (ask > 0) & (ask >= bid)
    sign = np.where(~quoted, 0, np.where(px >= ask, 1, np.where(px <= bid, -1, np.sign(px - mid))))
    t["sign"] = sign
    t["premium"] = px * t["size"] * 100
    bull = ((t.kind == "call") & (t.sign > 0)) | ((t.kind == "put") & (t.sign < 0))
    bear = ((t.kind == "put") & (t.sign > 0)) | ((t.kind == "call") & (t.sign < 0))
    out = {"contracts": int(t["size"].sum()), "premium": float(t["premium"].sum()),
           "trades": len(t), "signed_share": float((t.sign != 0).mean())}
    for thr in config.GRID["WHALE_MIN_PREMIUM"]:
        w = t["premium"] >= thr
        out[f"whale_bull_{thr}"] = float(t.loc[w & bull, "premium"].sum())
        out[f"whale_bear_{thr}"] = float(t.loc[w & bear, "premium"].sum())
        out[f"whale_n_{thr}"] = int((w & (bull | bear)).sum())
    # The single largest print, for the "which events" table.
    big = t.iloc[int(t["premium"].to_numpy().argmax())]
    out.update(largest_print_premium=float(big["premium"]), largest_print_symbol=str(big["symbol"]).replace(" ", ""),
               largest_print_side="bought" if big["sign"] > 0 else "sold" if big["sign"] < 0 else "mid")
    return out


def candidates(return_pool: bool = False) -> pd.DataFrame:
    """Stage-1 days sent to Databento: widest spike neighbour, contract floor, one-directional on volume, liquid,
    then a seeded random DATABENTO_SAMPLE_FRACTION of them (the same draw for in- and out-of-sample)."""
    s = pd.read_csv(config.DATA_DIR / "spike_days.csv")
    st = pd.read_parquet(config.DATA_DIR / "stock_daily.parquet").sort_values(["ticker", "date"])
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y-%m-%d")
    st["dv"] = st.close * st.volume
    g = st.groupby("ticker")
    st["dv20"] = g.dv.transform(lambda x: x.shift(1).rolling(20, min_periods=10).median())   # known before t
    st["px"] = g.close.shift(1)
    s = s.merge(st[["ticker", "date", "dv20", "px"]], on=["ticker", "date"], how="left")
    share = s.sampled_call / (s.sampled_call + s.sampled_put)
    m = ((s.spike_ratio >= min(config.GRID["SPIKE_MULT"])) & (s.sampled_volume >= config.SAMPLED_MIN_CONTRACTS)
         & ((share >= config.STAGE1_CALL_SHARE_EXTREME) | (share <= 1 - config.STAGE1_CALL_SHARE_EXTREME))
         & (s.px >= config.LIQ_MIN_PRICE) & (s.dv20 >= config.LIQ_MIN_DOLLAR_VOLUME))
    pool = s[m].reset_index(drop=True)
    if return_pool:
        return pool
    rng = np.random.default_rng(config.DATABENTO_SAMPLE_SEED)
    keep = rng.random(len(pool)) < config.DATABENTO_SAMPLE_FRACTION
    print(f"stage-1 pool {len(pool)} name-days; random {config.DATABENTO_SAMPLE_FRACTION:.0%} sample keeps {int(keep.sum())}")
    return pool[keep].reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quote-only", action="store_true", help="price the whole pass, buy nothing")
    ap.add_argument("--max-spend", type=float, default=config.DATABENTO_RUN_CAP)
    ap.add_argument("--abort-above", type=float, default=None, help="buy nothing if the total quote exceeds this")
    args = ap.parse_args()

    cand = candidates()
    db = Databento(run_cap=args.max_spend)
    windows = [rth_window(d) for d in cand.date]
    quotes, done = [], 0
    with ThreadPoolExecutor(6) as ex:
        for q in ex.map(lambda tw: db.quote("tcbbo", f"{tw[0]}.OPT", *tw[1]), zip(cand.ticker, windows)):
            quotes.append(q)
            done += 1
            if done % 250 == 0:
                print(f"  priced {done}/{len(cand)} days: ${sum(quotes):.2f} so far", flush=True)
    db.save_quotes()
    cand["quote_usd"] = quotes
    todo = cand[cand.quote_usd > 0]
    print(f"{len(cand)} candidate name-days; {len(todo)} not cached; quoted ${todo.quote_usd.sum():.2f} "
          f"(ledger: {db.ledger.summary()}; this run may spend ${args.max_spend:.2f})")
    if args.quote_only:
        return
    if args.abort_above is not None and todo.quote_usd.sum() > args.abort_above:
        print(f"ABORTED: quote ${todo.quote_usd.sum():.2f} is above the approved ${args.abort_above:.2f}; nothing bought")
        return

    if todo.quote_usd.sum() > db.ledger.run_remaining or todo.quote_usd.sum() > db.ledger.remaining:
        print(f"The pass would cost ${todo.quote_usd.sum():.2f}, above what this run may spend; buying the cheapest "
              f"days first until the guard stops it.")
    order = cand.sort_values("quote_usd").index          # cheapest first, so a cap leaves the most days covered

    def one(i):
        r, (s, e) = cand.loc[i], windows[i]
        try:
            df = db.fetch("tcbbo", f"{r.ticker}.OPT", s, e)
        except BudgetExceeded:
            return None
        except Exception as exc:                      # network failure after retries: skip the day, count it
            print(f"  {r.ticker} {r.date}: fetch failed ({type(exc).__name__}); excluded", flush=True)
            return None
        if "symbol" not in df.columns:
            df = df.reset_index()
        return {"ticker": r.ticker, "date": r.date, "spike_ratio": r.spike_ratio,
                "sampled_volume": r.sampled_volume, "quote_usd": r.quote_usd, **day_features(df)}

    with ThreadPoolExecutor(4) as ex:
        rows = [x for x in ex.map(one, order) if x is not None]
    skipped = len(cand) - len(rows)
    if skipped:
        print(f"{skipped} candidate days not bought (budget guard); they are excluded and counted in the results")
    out = pd.DataFrame(rows)
    out.to_csv(config.DATA_DIR / "whale_days.csv", index=False)
    print(f"{len(out)} name-days signed; {db.ledger.summary()}")


if __name__ == "__main__":
    main()
