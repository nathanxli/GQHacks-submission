"""Stage 3 · whale days → trade signals, with the Massive news check (HYPOTHESIS.md §2-3).

A whale day: full-chain contracts ≥ SPIKE_MIN_CONTRACTS, spike ratio ≥ SPIKE_MULT, and the dominant side's
share of whale premium (prints ≥ WHALE_MIN_PREMIUM) ≥ ONE_SIDED_MIN. Direction = the dominant side.

News: Massive articles from NEWS_LOOKBACK_DAYS before t through 09:30 ET on t+1 (the moment we could trade).
An article covers the name when one of its per-ticker insights names it. Status, relative to the whales'
direction: uncovered, covered_same (sentiment agrees: already public, no trade), covered_opposite,
covered_neutral. Baseline rule `not_same`: trade unless covered_same. Variant `uncovered_only`.

`news.csv` holds the lookup for every name-day and both directions, so variants never re-query.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import config
from sources import massive_get_all

ET = "America/New_York"


def news_status(ticker: str, day: str, sessions: pd.DatetimeIndex) -> dict:
    d = pd.Timestamp(day)
    i = sessions.searchsorted(d, side="right")
    nxt = sessions[i] if i < len(sessions) else d + pd.Timedelta(days=1)
    lo = pd.Timestamp(f"{d - pd.Timedelta(days=config.NEWS_LOOKBACK_DAYS):%Y-%m-%d} 00:00", tz=ET).tz_convert("UTC")
    hi = pd.Timestamp(f"{nxt:%Y-%m-%d} 09:30", tz=ET).tz_convert("UTC")
    arts = massive_get_all("/v2/reference/news", {"ticker": ticker, "limit": 1000, "order": "asc", "sort": "published_utc",
                                                  "published_utc.gte": lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                  "published_utc.lte": hi.strftime("%Y-%m-%dT%H:%M:%SZ")})
    sent = [ins.get("sentiment") for a in arts for ins in (a.get("insights") or [])
            if (ins.get("ticker") or "").upper() == ticker]
    return {"ticker": ticker, "date": day, "news_articles": len(sent),
            "news_pos": sent.count("positive"), "news_neg": sent.count("negative"), "news_neu": sent.count("neutral")}


def build_news(whales: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    path = config.DATA_DIR / "news.csv"
    have = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["ticker", "date"])
    done = set(zip(have.ticker, have.date))
    todo = [(t, d) for t, d in zip(whales.ticker, whales.date) if (t, d) not in done]
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda td: news_status(td[0], td[1], sessions), todo))
    news = pd.concat([have, pd.DataFrame(got)], ignore_index=True) if got else have
    news.to_csv(path, index=False)
    return news


def status_for(direction: np.ndarray, pos: pd.Series, neg: pd.Series, n: pd.Series) -> np.ndarray:
    same = np.where(direction > 0, pos > 0, neg > 0)
    opp = np.where(direction > 0, neg > 0, pos > 0)
    return np.where(n == 0, "uncovered", np.where(same, "covered_same", np.where(opp, "covered_opposite", "covered_neutral")))


def make_signals(whales: pd.DataFrame, news: pd.DataFrame, *, spike_mult=None, min_premium=None, one_sided=None,
                 min_contracts=None, news_rule: str = "not_same") -> pd.DataFrame:
    spike_mult = config.SPIKE_MULT if spike_mult is None else spike_mult
    min_premium = config.WHALE_MIN_PREMIUM if min_premium is None else min_premium
    one_sided = config.ONE_SIDED_MIN if one_sided is None else one_sided
    min_contracts = config.SPIKE_MIN_CONTRACTS if min_contracts is None else min_contracts
    w = whales.merge(news, on=["ticker", "date"], how="left")
    bull, bear = w[f"whale_bull_{min_premium}"], w[f"whale_bear_{min_premium}"]
    tot = bull + bear
    w["whale_premium"] = tot
    w["direction"] = np.where(bull >= bear, 1, -1)
    w["one_sided"] = np.where(tot > 0, np.maximum(bull, bear) / tot.where(tot > 0, 1), 0.0)
    w = w[(w.spike_ratio >= spike_mult) & (w.contracts >= min_contracts) & (tot > 0) & (w.one_sided >= one_sided)].copy()
    w["news_status"] = status_for(w.direction.to_numpy(), w.news_pos.fillna(0), w.news_neg.fillna(0), w.news_articles.fillna(0))
    if news_rule == "ignore":
        w["trade"] = True
    elif news_rule == "uncovered_only":
        w["trade"] = w.news_status == "uncovered"
    elif news_rule == "covered_same_only":            # the shadow book for prediction P2
        w["trade"] = w.news_status == "covered_same"
    else:
        w["trade"] = w.news_status != "covered_same"
    return w.sort_values("date").reset_index(drop=True)


SIGNAL_COLUMNS = ["ticker", "date", "direction", "spike_ratio", "contracts", "whale_premium", "one_sided",
                  "news_status", "news_articles", "trade", "largest_print_symbol", "largest_print_side",
                  "largest_print_premium"]
