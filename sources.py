"""Data access, one role per vendor (HYPOTHESIS.md): Massive = news + options reference/bars for the screen,
Databento = OPRA trades with top of book for the whale signal, Webull = stock/ETF bars for the backtest only.

Every response is cached under data/cache/ (gitignored), so a rerun costs nothing. Databento is metered:
every request is priced with `metadata.get_cost` first and checked against a persistent spend ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

import config

sys.path.insert(0, str(config.REPO_ROOT / "infrastructure"))
from eightk.budget import BudgetExceeded, SpendLedger  # noqa: E402

load_dotenv(config.ENV_FILE)
log = logging.getLogger("whale.sources")


def _key(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is not set; add it to infrastructure/backtest/.env (see .env.example)")
    return value


# ============================================================================ Massive
_local = threading.local()


def _massive_session() -> requests.Session:
    s = getattr(_local, "massive", None)
    if s is None:
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {_key('MASSIVE_API_KEY')}"
        _local.massive = s
    return s


def massive_get(path_or_url: str, params: dict | None = None) -> dict:
    """One cached GET against api.massive.com."""
    url = path_or_url if path_or_url.startswith("http") else "https://api.massive.com" + path_or_url
    full = requests.Request("GET", url, params=params).prepare().url
    cache = config.CACHE_DIR / "massive" / (hashlib.sha1(full.encode()).hexdigest() + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(10):
        r = _massive_session().get(full, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(2 ** attempt, 20))
            continue
        break
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(r.text)
    tmp.replace(cache)
    return r.json()


def massive_get_all(path: str, params: dict | None = None, max_pages: int = 200) -> list[dict]:
    payload = massive_get(path, params)
    rows = list(payload.get("results") or [])
    pages = 1
    while payload.get("next_url") and pages < max_pages:
        payload = massive_get(payload["next_url"])
        rows += payload.get("results") or []
        pages += 1
    return rows


# ============================================================================ Databento
class Databento:
    """OPRA trades-with-BBO (tcbbo), priced before purchase and capped by a persistent ledger."""

    DATASET = "OPRA.PILLAR"

    def __init__(self, run_cap: float = config.DATABENTO_RUN_CAP):
        import databento as db
        self.client = db.Historical(_key("DATABENTO_API_KEY"))
        self.ledger = SpendLedger(config.DATA_DIR / "databento_spend_ledger.json",
                                  total_budget_usd=config.DATABENTO_STUDY_CAP, run_budget_usd=run_cap)
        self.cache = config.CACHE_DIR / "databento"
        self.cache.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._reserved = 0.0                      # quoted cost of downloads in flight (threads)

    def _path(self, schema: str, parent: str, start: str, end: str) -> Path:
        digest = hashlib.sha1(f"{self.DATASET}|{schema}|{parent}|{start}|{end}".encode()).hexdigest()
        return self.cache / f"{digest}.parquet"

    def cached(self, schema: str, parent: str, start: str, end: str) -> bool:
        return self._path(schema, parent, start, end).exists()

    def _quotes(self) -> dict:
        if not hasattr(self, "_quote_cache"):
            p = self.cache / "quotes.json"
            self._quote_cache = json.loads(p.read_text()) if p.exists() else {}
            self._quote_dirty = 0
        return self._quote_cache

    def save_quotes(self):
        with self._lock:
            tmp = self.cache / "quotes.json.tmp"
            tmp.write_text(json.dumps(self._quotes()))
            tmp.replace(self.cache / "quotes.json")

    def quote(self, schema: str, parent: str, start: str, end: str) -> float:
        """Free: what this request would cost (cached on disk; retried on timeouts)."""
        if self.cached(schema, parent, start, end):
            return 0.0
        key = f"{schema}|{parent}|{start}|{end}"
        q = self._quotes()
        if key in q:
            return q[key]
        for attempt in range(5):
            try:
                cost = float(self.client.metadata.get_cost(dataset=self.DATASET, symbols=[parent], stype_in="parent",
                                                           schema=schema, start=start, end=end))
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        with self._lock:
            q[key] = cost
            self._quote_dirty += 1
        if self._quote_dirty % 100 == 0:
            self.save_quotes()
        return cost

    def fetch(self, schema: str, parent: str, start: str, end: str) -> pd.DataFrame:
        path = self._path(schema, parent, start, end)
        if path.exists():
            return pd.read_parquet(path)
        cost = self.quote(schema, parent, start, end)
        desc = f"{schema} {parent} {start}..{end}"
        with self._lock:                           # reserve before buying, so parallel requests cannot overshoot
            self.ledger.check(cost + self._reserved, desc)
            self._reserved += cost
        try:
            for attempt in range(4):
                try:
                    data = self.client.timeseries.get_range(dataset=self.DATASET, symbols=[parent], stype_in="parent",
                                                            schema=schema, start=start, end=end)
                    frame = data.to_df()
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(5 * (attempt + 1))
            frame.to_parquet(path)                 # cache first, then record the spend
            with self._lock:
                self.ledger.record(cost, desc, schema=schema, parent=parent)
        finally:
            with self._lock:
                self._reserved -= cost
        return frame


# ============================================================================ Webull (backtest only)
class WebullBars:
    """Daily adjusted stock/ETF bars from the Webull OpenAPI batch-bars route, cached per symbol and window."""

    def __init__(self):
        self.cache = config.CACHE_DIR / "webull"
        self.cache.mkdir(parents=True, exist_ok=True)
        self._client = None

    def client(self):
        if self._client is None:
            logging.disable(logging.CRITICAL)      # the SDK logs signed request headers on errors
            from webull.core.client import ApiClient
            from webull.data.data_client import DataClient
            region = os.environ.get("WEBULL_REGION_ID", "us")
            api = ApiClient(_key("WEBULL_APP_KEY"), _key("WEBULL_APP_SECRET"), region)
            api.add_endpoint(region, os.environ.get("WEBULL_API_ENDPOINT", "api.webull.com"))
            self._client = DataClient(api)
            for name in ("webull", "webull.core", "webull.core.client"):
                lg = logging.getLogger(name)
                lg.handlers.clear()
                lg.propagate = False
                lg.disabled = True
            logging.disable(logging.NOTSET)
        return self._client

    def daily(self, symbol: str, start: str, end: str) -> pd.DataFrame | None:
        """OHLCV indexed by session date; None when Webull does not carry the symbol (delisted/acquired)."""
        path = self.cache / f"{symbol}_{start}_{end}.json"
        if path.exists():
            payload = json.loads(path.read_text())
        else:
            ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
            client = self.client()                  # build first: client() re-enables logging when it finishes
            for attempt in range(8):
                logging.disable(logging.CRITICAL)   # the SDK logs full signed requests (incl. the app key) on errors
                try:
                    r = client.market_data.get_batch_history_bar(
                        symbols=[symbol], category="US_STOCK", timespan="D", count="1200", real_time_required=False,
                        start_time=ms(start), end_time=ms(end) + 86_400_000 - 1)
                    payload = {"status": r.status_code, "body": r.json()}
                except Exception as exc:            # unknown symbol is a 417 business error
                    payload = {"status": getattr(exc, "http_status", None), "error": str(getattr(exc, "error_msg", ""))[:200]}
                finally:
                    logging.disable(logging.NOTSET)
                if payload.get("status") != 429:
                    break
                time.sleep(min(2 ** attempt, 30))   # rate limited: back off and retry
            if payload.get("status") in (200, 417):
                path.write_text(json.dumps(payload))
            else:
                raise RuntimeError(f"Webull bars failed for {symbol}: {payload}")
            time.sleep(0.35)
        if payload.get("status") != 200:
            return None
        rows = (payload["body"].get("result") or [{}])[0].get("result") or []
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["time"].str[:10])
        df = df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()
        return df[~df.index.duplicated()]


__all__ = ["massive_get", "massive_get_all", "Databento", "BudgetExceeded", "WebullBars"]
