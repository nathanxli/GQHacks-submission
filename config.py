"""Locked study configuration. Mirrors HYPOTHESIS.md; change nothing here without logging a variant."""
from __future__ import annotations

from pathlib import Path

TRACK_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRACK_DIR.parent.parent
DATA_DIR = TRACK_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"            # raw vendor responses: gitignored, never committed
RESULTS_DIR = TRACK_DIR / "results"
ENV_FILE = REPO_ROOT / "infrastructure" / "backtest" / ".env"

# ---- Windows (locked) ------------------------------------------------------------------------
HISTORY_START, HISTORY_END = "2024-01-02", "2026-10-02"
IS_START, IS_END = "2024-01-02", "2026-03-13"
OOS_START, OOS_END = "2026-03-16", "2026-10-02"          # most recent 20%: run once, at the end

# ---- Whale day ---------------------------------------------------------------------------------
SPIKE_MULT = 3.0              # option volume ≥ 3× trailing median
SPIKE_LOOKBACK = 20           # sessions
SPIKE_MIN_CONTRACTS = 1_000    # full-chain contracts on t, counted from the Databento tape (stage 2)
# Stage-1 prefilters (Massive data, free). They only decide which days Databento is asked about; see DEVIATIONS.md.
SAMPLED_MIN_CONTRACTS = 1_000  # the 1,000-contract floor applied to the Massive sample (undercounts the chain)
STAGE1_CALL_SHARE_EXTREME = 0.75   # sampled call share ≥ 75% or ≤ 25%: a one-directional day on volume
LIQ_MIN_PRICE = 3.0            # tradeable names only (also a trading rule)
LIQ_MIN_DOLLAR_VOLUME = 5e6    # 20-day median stock dollar volume
DATABENTO_SAMPLE_FRACTION = 0.30   # seeded random share of candidate days signed on Databento (budget)
DATABENTO_SAMPLE_SEED = 20261003
WHALE_MIN_PREMIUM = 50_000    # $ per print
ONE_SIDED_MIN = 0.65          # dominant side's share of whale premium

# ---- News filter -------------------------------------------------------------------------------
NEWS_LOOKBACK_DAYS = 3        # calendar days before t, through the open of t+1

# ---- Trade -------------------------------------------------------------------------------------
HOLD_SESSIONS = 5
RISK_PER_POSITION = 0.01      # equity fraction per one-day sigma
MAX_WEIGHT = 0.10
MAX_ADV_FRACTION = 0.02
MAX_POSITIONS = 10
STOP_LOSS = -0.15
DRAWDOWN_DERISK = -0.15       # halve sizes while below this from peak
HEDGE_SYMBOL = "XBI"
HEDGE = True

# ---- Tail hedge (options, priced on real Massive option trades; see hedging.py and DEVIATIONS.md) ----
TAIL_HEDGE = True             # protective put on longs, call on shorts
TAIL_OTM = 0.10
TAIL_DTE_MIN, TAIL_DTE_TARGET, TAIL_DTE_MAX = 14, 30, 60
TAIL_SPREAD = 0.05            # fraction of premium paid per side on the option (×2 in the doubled-cost run)

# ---- Costs -------------------------------------------------------------------------------------
COST_BPS_STOCK = 20.0         # per side
COST_BPS_HEDGE = 2.0
BORROW_RATE_SHORT = 0.05      # annual
INITIAL_CASH = 1_000_000.0

# ---- Neighbours walked in the sensitivity grid (each run counts as a variant) ------------------
GRID = {
    "SPIKE_MULT": [2.0, 3.0, 4.0],
    "WHALE_MIN_PREMIUM": [25_000, 50_000, 100_000],
    "ONE_SIDED_MIN": [0.55, 0.65, 0.75],
    "HOLD_SESSIONS": [3, 5, 10],
}

# ---- Databento spend control -------------------------------------------------------------------
DATABENTO_TOTAL_BUDGET = 225.0
DATABENTO_STUDY_CAP = 100.0   # this study may spend at most this much, in total (raised by the team from $60)
DATABENTO_RUN_CAP = 100.0     # and at most this much per run
