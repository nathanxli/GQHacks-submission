# Hypothesis: whale footprints in biotech options, traded before the news

Written and committed **before any data for this study was pulled or any backtest was run**
(see this file's commit timestamp). Parameters below are fixed here; anything changed later is
counted as an extra variant and disclosed.

## The edge, in one sentence

When large, one-directional option trades ("whales") hit a biotech name **and the news has not yet
covered it**, the stock tends to move in the whales' direction over the next few sessions, because
the people trading know something the public does not yet, and because other traders who see the
flow pile in after it (a Keynesian beauty contest: price goes where the crowd expects the crowd to
push it).

## Who is on the other side, and why the edge persists

- **Option market makers** who sell the calls or puts. They hedge delta, so their hedging *adds* to
  the move rather than fighting it, and they are paid on spread, not direction.
- **Shareholders and short-sellers who have not seen the information yet.** Biotech value is
  dominated by binary, private-until-announced events (trial readouts, FDA decisions, partnering and
  M&A), which are known to a circle of insiders, advisers, CRO and site staff before disclosure.
- **It persists** because it is illegal or impractical for most of the informed side to trade the
  stock directly at size. Options give leverage and less visibility. Retail and slow institutional
  money reacts to *news*, not to option prints, so prices only fully adjust once coverage arrives.

## The rule (pre-specified)

1. **Universe.** The biotech ticker list in `data/biotech_tickers_raw.txt` (one primary symbol per
   line; for `A|B` groups the common-stock symbol). Kept: names with listed options and stock bars in
   the study window. Dead names stay in the list; they drop out only where no data exists.
2. **Whale day** (computed after the close of session *t*, from Databento OPRA data):
   - **Volume spike:** the name's total option volume on *t* is at least **3×** its trailing
     **20-session median**, and at least **1,000 contracts**.
   - **Whale prints:** option trades of at least **$50,000 premium** each. Each print is signed with
     the top of book at the moment it traded: at or above the ask = bought, at or below the bid =
     sold, in between = nearer side.
   - **Direction:** bullish premium = calls bought + puts sold; bearish = puts bought + calls sold.
     Direction is the larger side, and it must be at least **65%** of whale premium (one-directional).
3. **News check** (Massive news, `/v2/reference/news`, per-ticker sentiment): look from **3 calendar
   days before *t*** up to the **open of *t*+1** (the moment we could trade). If any article covering
   the ticker carries sentiment **in the whales' direction** (positive for bullish, negative for
   bearish), the information is already public: **no trade**. If not covered: **trade with the
   whales**.
4. **Trade** (backtested on Webull daily bars via backtrader): **buy** the stock on a bullish signal,
   **short** it on a bearish one, at the **open of *t*+1**; exit at the open **5 sessions later**.
5. **Sizing and risk:** each position sized to **1%** of equity per one-day standard deviation
   (20-session), capped at **10%** of equity per name and **2%** of the name's 20-day dollar volume;
   at most **10** positions; net exposure hedged with **XBI** (the biotech ETF); positions closed
   early at a **−15%** loss; all sizes halved while the strategy is more than **15%** below its peak.
6. **Costs:** **20 bps per side** on the stock (half-spread plus slippage for small and mid-cap
   biotech; to be checked against measured quotes), **2 bps** per side on XBI, **5% a year** borrow on
   shorts. Every result is also reported at **double** these costs.

## Data split (locked)

- History window: **2024-01-02 → 2026-10-02**.
- **Out-of-sample = the most recent 20%** (shorter than 2 years): **2026-03-16 → 2026-10-02**. Never
  used to design or tune; run **once**, at the end.
- In-sample: **2024-01-02 → 2026-03-13**.
- Data roles: **Databento** = option trades and top-of-book (signal); **Massive** = news and
  reference/options data (filter and screening); **Webull** = stock and ETF bars, **backtest only**.

## Testable predictions

1. **P1:** uncovered whale days earn a positive direction-signed return over the 5-session hold,
   net of costs, in-sample and out-of-sample.
2. **P2:** whale days the news already covered (same-direction sentiment) earn about zero. The news
   filter is what adds value: uncovered minus covered > 0.
3. **P3:** the edge grows with the size of the one-directional whale premium (terciles are monotone).
4. **P4:** the edge is front-loaded (1–5 sessions) and fades by 10–20.

## It fails if

- P1 is not positive out-of-sample, or the sign flips at neighbouring thresholds (2×/4× spike,
  $25k/$100k prints, 55%/75% one-directional, 3/10-session holds).
- The returns are explained by XBI beta (hedged returns ≈ 0).
- The edge disappears at double costs or in names liquid enough to trade at size.
