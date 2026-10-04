# Changes made after the hypothesis commit (16e75ab), and why

All of these were decided **before any return, P&L or backtest of a real signal was computed**. Each is an
implementation constraint, not a tuning choice; anything tuned on results is listed in `results/variants_log.csv`.

| # | Change | Reason |
|---|---|---|
| 1 | **Screen on Massive, sign on Databento.** The 3× / 1,000-contract spike is first screened on a Massive sample (ATM, ±10%, ±20% calls and puts on each monthly expiry, strikes chosen from the close six weeks before expiry); Databento `tcbbo` (OPRA has no `tbbo`) is bought only for flagged days | Databento quoted $52 for one name's daily option bars over the window ($0.56/MB); a full-universe Databento screen would cost thousands |
| 2 | **Stage-1 prefilters**: sampled volume ≥ 1,000 contracts; sampled call share ≥ 75% or ≤ 25%; stock price ≥ $3 and 20-day median dollar volume ≥ $5M (both measured before t) | Even after (1), the sampled series flags ~2% of name-days; signing all of them would cost ~$150. The one-directional proxy mirrors §2's direction test; the liquidity floor mirrors the "liquid enough to trade at size" kill criterion. The 1,000-contract floor on the full Databento tape still applies in stage 2 |
| 3 | **Seeded random 30% of the stage-1 pool is signed on Databento** (seed 20261003), the same draw for in-sample and out-of-sample | Budget: the team capped this study's Databento spend at $100 of its $225. Random sampling lowers the number of trades, not the expected edge |
| 4 | Pool built at the **widest spike neighbour (2×)** | So the 2× / 3× / 4× sensitivity variants are covered by one purchase |
| 5 | Webull bars come from the **sandbox** OpenAPI host (`api.sandbox.webull.com`, batch-bars route) | The team's keys are sandbox keys; that host serves real daily bars on this route |
| 6 | Names **Webull does not carry** (delisted or acquired before October 2026) cannot be traded in the backtest | Webull returns "symbol does not exist" for them. Their signals are counted in `results/*_summary.json` (`webull_missing_symbols`) and excluded |
| 7 | **Options tail hedge added to the traded book** (protective ~10% OTM put on longs / call on shorts, ~30 days to expiry, priced at the day's VWAP on real Massive option trades plus 5% of premium per side). Reported with and without | Added at the team's request for the risk-management criterion, before any signal return was computed: the −15% stop cannot protect against biotech's overnight gaps on binary events |
