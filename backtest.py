"""Stage 4 · the backtest: backtrader on Webull daily bars (the only price source used for P&L).

Rules (HYPOTHESIS.md §4-6): a signal on session t is acted on at the close of t, so the order fills at the
open of t+1; the position is closed by an order at the close of t+HOLD, filling at the open of t+HOLD+1
(HOLD sessions open to open). Sizing: RISK_PER_POSITION / σ20 of equity, capped at MAX_WEIGHT and at
MAX_ADV_FRACTION of 20-day share volume; at most MAX_POSITIONS; sizes halved while the book is more than
DRAWDOWN_DERISK below its peak; a position that closes STOP_LOSS or worse is exited at the next open; net
stock exposure is hedged daily with XBI. Costs: COST_BPS per side as commission (×cost_mult), borrow on
short notional charged daily.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import backtrader as bt
import numpy as np
import pandas as pd

import config
from hedging import TailHedge
from sources import WebullBars


@dataclass
class Params:
    hold: int = config.HOLD_SESSIONS
    risk: float = config.RISK_PER_POSITION
    max_weight: float = config.MAX_WEIGHT
    max_adv: float = config.MAX_ADV_FRACTION
    max_positions: int = config.MAX_POSITIONS
    stop: float = config.STOP_LOSS
    derisk: float = config.DRAWDOWN_DERISK
    hedge: bool = config.HEDGE
    tail_hedge: bool = config.TAIL_HEDGE
    cost_mult: float = 1.0
    cash: float = config.INITIAL_CASH


class PctComm(bt.CommInfoBase):
    params = (("commission", 0.002), ("stocklike", True), ("commtype", bt.CommInfoBase.COMM_PERC), ("percabs", True))


class WhaleStrategy(bt.Strategy):
    params = (("signals", None), ("p", None), ("hedge_name", config.HEDGE_SYMBOL))

    def __init__(self):
        self.P: Params = self.p.p
        self.by_name = {d._name: d for d in self.datas}
        self.hedge = self.by_name.get(self.p.hedge_name)
        self.sig = self.p.signals                     # {date: [(ticker, direction, meta)]}
        self.open = {}                                # ticker -> dict(entry_len, direction, entry_price, meta)
        self.pending_exit = set()
        self.peak = self.P.cash
        self.equity, self.traded, self.trades, self.skipped = [], [], [], []
        self.hedge_notes = []

    # backtrader only calls next() once every feed has a bar; names list at different times, so run early too.
    def prenext(self):
        self.next()

    def _today(self):
        return self.hedge.datetime.date(0)

    def _has_bar(self, d) -> bool:
        return len(d) > 0 and d.datetime.date(0) == self._today()

    def notify_order(self, order):
        if order.status != order.Completed:
            return
        name = order.data._name
        self.traded.append((self._today(), abs(order.executed.size * order.executed.price)))
        if name == self.p.hedge_name:
            return
        if name in self.open and self.open[name].get("entry_price") is None:
            pos = self.open[name]
            pos["entry_price"] = order.executed.price
            pos["entry_date"] = order.data.datetime.date(0)
            if self.P.tail_hedge:
                h = TailHedge(name, pos["entry_date"], pos["direction"], order.executed.price, pos["shares"],
                              config.TAIL_SPREAD * self.P.cost_mult)
                if h.ok:
                    self.broker.add_cash(-h.cost)
                    pos["hedge"] = h
                    self.traded.append((self._today(), h.cost))
                self.hedge_notes.append({"ticker": name, "date": pos["entry_date"], "hedged": h.ok, "reason": h.reason,
                                         "option": getattr(h, "option", None), "premium_pct_of_position":
                                         (h.cost / (pos["shares"] * order.executed.price)) if h.ok else None})
        elif name in self.pending_exit:
            pos = self.open.pop(name)
            self.pending_exit.discard(name)
            ret = pos["direction"] * (order.executed.price / pos["entry_price"] - 1)
            hedge_pnl = 0.0
            h = pos.get("hedge")
            if h is not None:
                cash, _ = h.proceeds(order.data.datetime.date(0))
                self.broker.add_cash(cash)
                self.traded.append((self._today(), cash))
                hedge_pnl = cash - h.cost
            notional = pos["shares"] * pos["entry_price"]
            self.trades.append({**pos["meta"], "hedge_pnl_pct": hedge_pnl / notional, "hedged": h is not None,
                                "return_with_hedge": ret + hedge_pnl / notional,
                                "ticker": name, "direction": pos["direction"],
                                "entry_date": pos["entry_date"], "entry_price": pos["entry_price"],
                                "exit_date": order.data.datetime.date(0), "exit_price": order.executed.price,
                                "shares": pos["shares"], "gross_return": ret, "exit_reason": pos.get("reason", "hold")})

    def next(self):
        today = self._today()
        value = self.broker.getvalue() + sum(p["hedge"].mark(today) for p in self.open.values() if p.get("hedge"))
        self.peak = max(self.peak, value)
        self.equity.append((today, value))

        # Borrow on short notional, charged daily.
        short_notional = sum(-self.getposition(d).size * d.close[0] for d in self.datas
                             if self._has_bar(d) and self.getposition(d).size < 0 and d is not self.hedge)
        hedge_short = -self.getposition(self.hedge).size * self.hedge.close[0] if self.getposition(self.hedge).size < 0 else 0.0
        fee = short_notional * config.BORROW_RATE_SHORT / 252 + hedge_short * 0.005 / 252
        if fee:
            self.broker.add_cash(-fee)

        # Exits: holding period, stop loss, or the name stopped trading.
        for name, pos in list(self.open.items()):
            d = self.by_name[name]
            if name in self.pending_exit or pos.get("entry_price") is None:
                continue
            if not self._has_bar(d):
                if len(d) and d.datetime.date(0) < today:          # feed ended (delisted mid-hold): flat at last close
                    self.close(data=d, exectype=bt.Order.Market)
                    pos["reason"] = "feed_ended"
                    self.pending_exit.add(name)
                continue
            held = len(d) - pos["signal_len"]
            move = pos["direction"] * (d.close[0] / pos["entry_price"] - 1)
            if held >= self.P.hold or move <= self.P.stop:
                pos["reason"] = "hold" if held >= self.P.hold else "stop"
                self.close(data=d)
                self.pending_exit.add(name)

        # Entries for signals dated today (acted on at today's close -> next open).
        derisk = 0.5 if value < self.peak * (1 + self.P.derisk) else 1.0
        for ticker, direction, meta in self.sig.get(today, []):
            d = self.by_name.get(ticker)
            if d is None or not self._has_bar(d):
                self.skipped.append({**meta, "ticker": ticker, "reason": "no Webull bar on signal day"})
                continue
            if ticker in self.open:
                self.skipped.append({**meta, "ticker": ticker, "reason": "already holding"})
                continue
            if len(self.open) >= self.P.max_positions:
                self.skipped.append({**meta, "ticker": ticker, "reason": "position limit"})
                continue
            if len(d) < 21:
                self.skipped.append({**meta, "ticker": ticker, "reason": "under 20 sessions of history"})
                continue
            closes = np.array([d.close[-i] for i in range(21)])[::-1]
            sigma = float(np.std(np.diff(np.log(closes)), ddof=1))
            adv_shares = float(np.mean([d.volume[-i] for i in range(20)]))
            if not (sigma > 0 and adv_shares > 0):
                self.skipped.append({**meta, "ticker": ticker, "reason": "no volatility/volume history"})
                continue
            weight = min(self.P.max_weight, self.P.risk / sigma) * derisk
            shares = min(weight * value / d.close[0], self.P.max_adv * adv_shares)
            shares = math.floor(shares)
            if shares < 1:
                self.skipped.append({**meta, "ticker": ticker, "reason": "size rounds to zero"})
                continue
            (self.buy if direction > 0 else self.sell)(data=d, size=shares)
            self.open[ticker] = {"direction": direction, "signal_len": len(d), "entry_price": None, "shares": shares,
                                 "meta": {**meta, "weight": weight, "sigma20": sigma, "adv_shares": adv_shares,
                                          "adv_participation": shares / adv_shares}}

        # Hedge the net stock exposure with XBI (orders fill at the next open, like everything else).
        if self.P.hedge and self._has_bar(self.hedge):
            net = sum(self.getposition(d).size * d.close[0] for d in self.datas if d is not self.hedge and self._has_bar(d))
            target = -round(net / self.hedge.close[0])
            cur = self.getposition(self.hedge).size
            if abs(target - cur) * self.hedge.close[0] > 0.01 * value:
                self.order_target_size(data=self.hedge, target=target)


def load_bars(tickers: list[str], start: str, end: str) -> tuple[dict, list[str]]:
    wb = WebullBars()
    pad_start = (pd.Timestamp(start) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    pad_end = min(pd.Timestamp(end) + pd.Timedelta(days=30), pd.Timestamp(config.HISTORY_END) + pd.Timedelta(days=0)).strftime("%Y-%m-%d")
    bars, missing = {}, []
    for t in sorted(set(tickers) | {config.HEDGE_SYMBOL}):
        df = wb.daily(t, pad_start, pad_end)
        if df is None or len(df) < 5:
            missing.append(t)
        else:
            bars[t] = df
    return bars, missing


def run(signals: pd.DataFrame, start: str, end: str, P: Params | None = None, bars: dict | None = None) -> dict:
    """One backtest over [start, end] using the signals dated inside it."""
    P = P or Params()
    sig = signals[(signals.date >= start) & (signals.date <= end) & signals.trade].copy()
    if bars is None:
        bars, missing = load_bars(sig.ticker.unique().tolist(), start, end)
    else:
        missing = [t for t in sig.ticker.unique() if t not in bars]
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(P.cash)
    cerebro.broker.set_coc(False)
    hedge_df = bars[config.HEDGE_SYMBOL]
    window = (hedge_df.index >= pd.Timestamp(start) - pd.Timedelta(days=45)) & (hedge_df.index <= pd.Timestamp(end) + pd.Timedelta(days=30))
    cerebro.adddata(bt.feeds.PandasData(dataname=hedge_df[window]), name=config.HEDGE_SYMBOL)
    cerebro.broker.addcommissioninfo(PctComm(commission=config.COST_BPS_HEDGE / 1e4 * P.cost_mult), name=config.HEDGE_SYMBOL)
    for t in sig.ticker.unique():
        if t in bars:
            df = bars[t]
            df = df[(df.index >= pd.Timestamp(start) - pd.Timedelta(days=45)) & (df.index <= pd.Timestamp(end) + pd.Timedelta(days=30))]
            if len(df):
                cerebro.adddata(bt.feeds.PandasData(dataname=df), name=t)
                cerebro.broker.addcommissioninfo(PctComm(commission=config.COST_BPS_STOCK / 1e4 * P.cost_mult), name=t)
    schedule = {}
    for r in sig.itertuples():
        meta = {"signal_date": r.date, "news_status": r.news_status, "whale_premium": r.whale_premium,
                "one_sided": r.one_sided, "spike_ratio": r.spike_ratio}
        schedule.setdefault(pd.Timestamp(r.date).date(), []).append((r.ticker, int(r.direction), meta))
    cerebro.addstrategy(WhaleStrategy, signals=schedule, p=P)
    strat = cerebro.run(runonce=False)[0]
    eq = pd.Series(dict(strat.equity), dtype=float)
    eq.index = pd.to_datetime(eq.index)
    eq = eq[(eq.index >= pd.Timestamp(start)) & (eq.index <= pd.Timestamp(end) + pd.Timedelta(days=30))]
    traded = pd.DataFrame(strat.traded, columns=["date", "notional"])
    return {"equity": eq, "trades": pd.DataFrame(strat.trades), "skipped": pd.DataFrame(strat.skipped),
            "hedge_notes": pd.DataFrame(strat.hedge_notes),
            "traded": traded, "missing": missing, "n_signals": len(sig), "params": P}
