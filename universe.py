"""Clean the raw biotech list to one symbol per company, then keep names with listed options in the study window.

Options listing comes from Massive's contracts reference (the role HYPOTHESIS.md gives Massive). Dead names
stay in `biotech_tickers_raw.txt`; they drop out here only because no contracts existed in the window.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import config


def primary_symbol(line: str) -> str | None:
    """'OTRAU|OTRA|OTRAW' -> 'OTRA': the shortest token without a '.' (units, warrants and rights are longer)."""
    tokens = [t.strip().upper() for t in line.split("|") if t.strip()]
    plain = [t for t in tokens if "." not in t and "-" not in t] or tokens
    return min(plain, key=lambda t: (len(t), plain.index(t))) if plain else None


def clean_list() -> list[str]:
    seen, out = set(), []
    for line in (config.DATA_DIR / "biotech_tickers_raw.txt").read_text().splitlines():
        sym = primary_symbol(line)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def massive_key() -> str:
    for path in (config.ENV_FILE, config.REPO_ROOT / "massive" / "gqh-massive-8k-starter" / ".env"):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith("MASSIVE_API_KEY=") and line.split("=", 1)[1].strip():
                    return line.split("=", 1)[1].strip()
    sys.exit("MASSIVE_API_KEY not found in infrastructure/backtest/.env")


def massive_get(session: requests.Session, url: str, params: dict) -> dict:
    full = requests.Request("GET", url, params=params).prepare().url
    cache = config.CACHE_DIR / "massive" / (hashlib.sha1(full.encode()).hexdigest() + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    for attempt in range(8):
        r = session.get(full, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(2 ** attempt, 20))
            continue
        r.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(r.text)
        return r.json()
    r.raise_for_status()


def options_listed(session, sym: str) -> dict:
    """Contracts listed at three dates across the window (start, middle, OOS start)."""
    out = {"symbol": sym}
    for label, day in (("listed_2024", "2024-01-02"), ("listed_2025", "2025-02-03"), ("listed_oos", config.OOS_START)):
        p = massive_get(session, "https://api.massive.com/v3/reference/options/contracts",
                        {"underlying_ticker": sym, "as_of": day, "limit": 1})
        out[label] = int(bool(p.get("results")))
    return out


def main():
    syms = clean_list()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {massive_key()}"
    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda s: options_listed(session, s), syms))
    rows = [dict(r, any_listed=int(r["listed_2024"] or r["listed_2025"] or r["listed_oos"])) for r in rows]
    with open(config.DATA_DIR / "universe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n = sum(r["any_listed"] for r in rows)
    print(f"{len(syms)} unique symbols; {n} had listed options at some point in the window "
          f"({sum(r['listed_2024'] for r in rows)} in Jan 2024, {sum(r['listed_oos'] for r in rows)} at OOS start)")


if __name__ == "__main__":
    main()
