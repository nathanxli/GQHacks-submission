"""Tail hedge: a protective option on every position, priced from real Massive option trades.

Biotech's dominant risk is the overnight gap on a binary event (trial readout, FDA decision, financing),
which a stop-loss cannot protect against because the stock opens through it. Each long position buys a put
~TAIL_OTM below spot; each short buys a call ~TAIL_OTM above (a squeeze is the short's tail). Expiry: the
listed expiry nearest TAIL_DTE_TARGET days, between TAIL_DTE_MIN and TAIL_DTE_MAX, so it outlives the hold.
Contracts = position shares / 100 (rounded). Fills at the day's VWAP on Massive daily option bars, plus
TAIL_SPREAD of premium per side; marked daily at the last close (at most 5 sessions stale), sold on the
stock's exit day. The stock leg is still traded only on Webull bars.
"""
from __future__ import annotations

import pandas as pd

import config
from sources import massive_get_all


def choose_contract(ticker: str, day: pd.Timestamp, direction: int, spot: float) -> dict | None:
    kind = "put" if direction > 0 else "call"
    rows = massive_get_all("/v3/reference/options/contracts", {
        "underlying_ticker": ticker, "as_of": f"{day:%Y-%m-%d}", "contract_type": kind, "limit": 1000,
        "expiration_date.gte": f"{day + pd.Timedelta(days=config.TAIL_DTE_MIN):%Y-%m-%d}",
        "expiration_date.lte": f"{day + pd.Timedelta(days=config.TAIL_DTE_MAX):%Y-%m-%d}"})
    if not rows:
        return None
    c = pd.DataFrame(rows)
    if "shares_per_contract" in c:
        c = c[c["shares_per_contract"].fillna(100) == 100]
    if c.empty:
        return None
    c["expiration_date"] = pd.to_datetime(c["expiration_date"])
    c["dte"] = (c["expiration_date"] - day).dt.days
    exp = c.loc[(c["dte"] - config.TAIL_DTE_TARGET).abs().idxmin(), "expiration_date"]
    e = c[c["expiration_date"] == exp]
    target = spot * (1 - config.TAIL_OTM) if direction > 0 else spot * (1 + config.TAIL_OTM)
    k = e.loc[(e["strike_price"] - target).abs().idxmin()]
    return {"option": k["ticker"], "kind": kind, "strike": float(k["strike_price"]), "expiry": exp}


def option_bars(option: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = massive_get_all(f"/v2/aggs/ticker/{option}/range/1/day/{start:%Y-%m-%d}/{end:%Y-%m-%d}",
                           {"adjusted": "false", "sort": "asc", "limit": 50000})
    if not rows:
        return pd.DataFrame(columns=["vwap", "close", "volume"])
    idx = pd.to_datetime([r["t"] for r in rows], unit="ms", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    return pd.DataFrame({"vwap": [float(r.get("vw") or r["c"]) for r in rows], "close": [float(r["c"]) for r in rows],
                         "volume": [float(r.get("v") or 0) for r in rows]}, index=idx)


class TailHedge:
    """One protective option position attached to a stock position."""

    def __init__(self, ticker: str, entry_day, direction: int, spot: float, shares: int, spread: float):
        self.ok, self.reason = False, ""
        day = pd.Timestamp(entry_day)
        self.n = int(round(shares / 100))
        if self.n < 1:
            self.reason = "position under 50 shares"
            return
        c = choose_contract(ticker, day, direction, spot)
        if c is None:
            self.reason = "no listed contract in the expiry window"
            return
        self.__dict__.update(c)
        self.bars = option_bars(self.option, day, min(self.expiry, day + pd.Timedelta(days=40)))
        on_day = self.bars[self.bars.index == day]
        if on_day.empty or on_day["vwap"].iloc[0] <= 0:
            self.reason = "hedge contract did not trade on the entry day"
            return
        self.spread = spread
        self.entry_px = float(on_day["vwap"].iloc[0])
        self.cost = self.n * 100 * self.entry_px * (1 + spread)          # cash paid
        self.ok = True

    def mark(self, day) -> float:
        b = self.bars.loc[: pd.Timestamp(day)]
        if b.empty:
            return 0.0
        return self.n * 100 * float(b["close"].iloc[-1])

    def proceeds(self, day) -> tuple[float, float]:
        """Cash received selling on `day` (VWAP if it traded, else last close), and the price used."""
        d = pd.Timestamp(day)
        on = self.bars[self.bars.index == d]
        px = float(on["vwap"].iloc[0]) if len(on) else (float(self.bars.loc[:d, "close"].iloc[-1]) if len(self.bars.loc[:d]) else 0.0)
        return self.n * 100 * px * (1 - self.spread), px
