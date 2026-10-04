"""Stage 1 · volume-spike screen on Massive (no Databento spend).

For every optionable name, a sampled option-volume series: on each standard monthly expiry, the contracts
nearest ATM, ±10% and ±20% (calls above, puts below, one each in the money), chosen from the stock's close
about six weeks before expiry, so the choice never looks ahead. Their daily volumes are summed per name.
A spike day is one whose sampled volume is at least SPIKE_MULT × the trailing SPIKE_LOOKBACK-session
median. Spike days go to stage 2, where Databento's full trade tape confirms size and signs the flow.

Outputs: data/stock_daily.parquet (Massive grouped daily bars, for spot and liquidity), data/option_volume.parquet,
data/spike_days.csv.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import config
from sources import massive_get, massive_get_all

TARGETS = [("call", 0.9), ("call", 1.0), ("call", 1.1), ("call", 1.2),
           ("put", 1.1), ("put", 1.0), ("put", 0.9), ("put", 0.8)]
SCREEN_START = "2023-11-01"          # baseline history before the study window opens
MEDIAN_FLOOR = 10                    # contracts; a dead series cannot spike off a zero median


def sessions(start: str, end: str) -> list[pd.Timestamp]:
    # Weekdays; holidays simply return an empty grouped file and are dropped.
    return list(pd.bdate_range(start, end))


def grouped_daily(universe: set[str]) -> pd.DataFrame:
    path = config.DATA_DIR / "stock_daily.parquet"
    if path.exists():
        return pd.read_parquet(path)
    days = sessions(SCREEN_START, config.HISTORY_END)

    def one(d):
        p = massive_get(f"/v2/aggs/grouped/locale/us/market/stocks/{d:%Y-%m-%d}", {"adjusted": "true"})
        return [(r["T"], d, r.get("o"), r.get("h"), r.get("l"), r.get("c"), r.get("v")) for r in p.get("results") or []
                if r.get("T") in universe]

    rows = []
    with ThreadPoolExecutor(8) as ex:
        for got in ex.map(one, days):
            rows += got
    df = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "volume"])
    df.to_parquet(path)
    return df


def is_monthly(d: pd.Timestamp) -> bool:
    return 15 <= d.day <= 21 and d.weekday() in (3, 4)


def sampled_volume(ticker: str, closes: pd.Series) -> pd.DataFrame | None:
    """Daily sampled call and put volume for one name."""
    contracts = []
    for expired in ("true", "false"):
        contracts += massive_get_all("/v3/reference/options/contracts", {
            "underlying_ticker": ticker, "expired": expired, "limit": 1000,
            "expiration_date.gte": "2023-12-01", "expiration_date.lte": "2026-11-30"})
    if not contracts:
        return None
    c = pd.DataFrame(contracts)
    if c.empty or "expiration_date" not in c:
        return None
    if "shares_per_contract" in c:
        c = c[c["shares_per_contract"].fillna(100) == 100]
    c["expiration_date"] = pd.to_datetime(c["expiration_date"])
    c = c[c["expiration_date"].map(is_monthly)]
    # Thursday expiries only where that Friday was a holiday: keep one expiry per month (the latest).
    c = c[c["expiration_date"] == c.groupby(c["expiration_date"].dt.to_period("M"))["expiration_date"].transform("max")]
    chosen = []
    for expiry, g in c.groupby("expiration_date"):
        ref = closes.loc[: expiry - pd.Timedelta(days=42)]
        if ref.empty:
            continue
        spot = float(ref.iloc[-1])
        for kind, m in TARGETS:
            side = g[g["contract_type"] == kind]
            if side.empty:
                continue
            k = side.iloc[(side["strike_price"] - spot * m).abs().argmin()]
            chosen.append((k["ticker"], kind, expiry))
    if not chosen:
        return None
    frames = []
    for opt, kind, expiry in dict.fromkeys(chosen):
        start = max(expiry - pd.Timedelta(days=63), pd.Timestamp(SCREEN_START))
        end = min(expiry, pd.Timestamp(config.HISTORY_END))
        if start > end:
            continue
        rows = massive_get_all(f"/v2/aggs/ticker/{opt}/range/1/day/{start:%Y-%m-%d}/{end:%Y-%m-%d}",
                               {"adjusted": "false", "sort": "asc", "limit": 50000})
        if rows:
            frames.append(pd.DataFrame({"date": pd.to_datetime([r["t"] for r in rows], unit="ms").normalize(),
                                        "kind": kind, "volume": [float(r.get("v") or 0) for r in rows]}))
    if not frames:
        return None
    v = pd.concat(frames).pivot_table(index="date", columns="kind", values="volume", aggfunc="sum").fillna(0.0)
    v = v.reindex(columns=["call", "put"], fill_value=0.0)
    v["ticker"] = ticker
    return v.reset_index()


def find_spikes(vol: pd.DataFrame, stock: pd.DataFrame) -> pd.DataFrame:
    out = []
    for ticker, g in vol.groupby("ticker"):
        days = pd.DatetimeIndex(sorted(stock.loc[stock.ticker == ticker, "date"].unique()))
        g = g.set_index("date")[["call", "put"]].reindex(days, fill_value=0.0)
        total = g.sum(axis=1)
        base = total.shift(1).rolling(config.SPIKE_LOOKBACK, min_periods=config.SPIKE_LOOKBACK).median().clip(lower=MEDIAN_FLOOR)
        ratio = total / base
        hit = (ratio >= min(config.GRID["SPIKE_MULT"])) & (total.index >= pd.Timestamp(config.HISTORY_START))
        for d in total.index[hit]:
            out.append({"ticker": ticker, "date": d.date().isoformat(), "sampled_volume": total[d],
                        "sampled_call": g.loc[d, "call"], "sampled_put": g.loc[d, "put"],
                        "baseline_median": base[d], "spike_ratio": ratio[d]})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="first N names only (smoke test)")
    args = ap.parse_args()
    uni = pd.read_csv(config.DATA_DIR / "universe.csv")
    all_names = uni.loc[uni.any_listed == 1, "symbol"].tolist()
    names = all_names[: args.limit]
    stock = grouped_daily(set(all_names) | {config.HEDGE_SYMBOL, "SPY"})   # always the full universe
    print(f"stock bars: {len(stock):,} rows, {stock.ticker.nunique()} names")
    closes = {t: g.set_index("date")["close"].sort_index() for t, g in stock.groupby("ticker")}

    frames, done = [], 0
    with ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(sampled_volume, t, closes[t]): t for t in names if t in closes}
        for f in as_completed(futs):
            done += 1
            try:
                v = f.result()
                if v is not None:
                    frames.append(v)
            except Exception as e:
                print(f"  {futs[f]} failed: {type(e).__name__}: {e}")
            if done % 50 == 0:
                print(f"  sampled {done}/{len(futs)} names")
    vol = pd.concat(frames, ignore_index=True)
    vol.to_parquet(config.DATA_DIR / "option_volume.parquet")
    spikes = find_spikes(vol, stock)
    spikes.to_csv(config.DATA_DIR / "spike_days.csv", index=False)
    in_is = spikes[spikes.date <= config.IS_END]
    print(f"{vol.ticker.nunique()} names with sampled option volume; "
          f"{len(spikes):,} days at ≥{min(config.GRID['SPIKE_MULT'])}× ({len(in_is):,} in-sample), "
          f"{(spikes.spike_ratio >= config.SPIKE_MULT).sum():,} at the baseline {config.SPIKE_MULT}×")


if __name__ == "__main__":
    main()
