# Massive 8-K Track

This is our submission to the Massive challenge. 

We pursue two approaches that utilize data from the 8-K filings as well as the provided options trading strategies. We aggregated these two into one final writeup, but kept the exploration notebooks separate as directed by Christian (the GOAT; thank you so much for answering a bajillion questions).

Below are instructions on how to run each of the notebooks. 

## 1. Small-cap Biotech Trial Results

### Run it as is

1. `./setup.sh` (Windows: `powershell -ExecutionPolicy Bypass -File setup.ps1`). Creates `.venv`,
   installs `requirements.txt`, registers the kernel "Python (Gator Quant Hacks .venv)", creates `.env`
2. Put your Massive key in `.env`: `MASSIVE_API_KEY=...`
3. Open the notebook, pick that kernel, run all cells.

No Jev API access needed. All Jev answers for the in-sample (2024–2025) and out-of-sample (2026-01..08) filings are committed in `jev_cache/`. 

### Run it on a new time window 

A new window contains filings that are not in `jev_cache/`, so the notebook labels them live with
Jev. For that it needs the TypeSafe key in the same `.env` file:

    MASSIVE_API_KEY=...
    TYPESAFE_API_KEY=...

**NOTE**: We have sent our TypeSafe API key to Christian. Feel free to use that to run the test. 

Then, in section 2 (Configuration), set

    HOLDOUT_START, HOLDOUT_END = "<start>", "<end>"
    RUN_HOLDOUT = True

and run all cells. 

Section 16 runs the entire chain on that window through `run_study(start, end)`:
events → EDGAR text → Jev → stock and option data → signal → P&L, with every parameter fitted
in-sample and frozen, and prints the same read-out as the out-of-sample section next to the in-sample
result. 

The model is pinned (`jev-1.13.0`), so live labels are on the same scale as the cached ones.

If the key is missing, the notebook stops at the first uncached filing with a message saying so.


## 2. Factory Disruptions Against Satellite Data

### Run it as is

1. Same setup as above: `./setup.sh`, kernel "Python (Gator Quant Hacks .venv)", `MASSIVE_API_KEY=...` in `.env`.
   No other keys are needed.
2. Keep `disruption_events.json` in the same folder as the notebook. It holds the 45 disruption 8-Ks
   (2020-01 to 2026-09), each pre-labelled `brief` / `persistent` / `unknown` from NASA FIRMS.
3. Open the notebook from its own folder, pick the kernel, run all cells.

Section 1 should print `API key loaded (ends xxxx)` and section 2 `Events path: ... (exists=True)`.


### Run it on a new time window

In section 2 (Configuration), set

    HOLDOUT_START, HOLDOUT_END = "<start>", "<end>"
    RUN_HOLDOUT = True

and run all cells. The last section runs the whole chain on that window through `run_study(start, end)`:
events → sessions → option chains → P&L → scoreboard, with every parameter frozen, next to the
in-sample board.

**NOTE**: Unlike the biotech notebook, this one cannot label new events live. The window is limited
to what is already in `disruption_events.json`. See below.

### Window constraints

The `brief` / `persistent` label on each event comes from NASA FIRMS satellite hotspot data at the
geocoded plant site. That labelling was run offline, once, and its results are stored in
`disruption_events.json`. The notebook reads those stored labels; it does not query NASA, and
`FIRMS_MAP_KEY` is not needed to run it.

The reason it is offline: labelling a new filing needs the plant located from the filing text
(OpenStreetMap geocoding), a FIRMS query for that site, and the right FIRMS product for the date
(near-real-time for recent dates, standard-processing for older ones). That pipeline lives in our
research repo, not in this notebook.

What this means for a new window:

- Inside 2020-01 to 2026-09 the notebook runs on whichever `brief` events fall in the window.
  Expect small counts: the default sealed window (2026-01 to 2026-06) has 3 events before option
  liquidity drops.
- Outside that range there are no labelled events, so the notebook prints `n = 0` and skips the plot.
  It does not fail.
- Leave `REFRESH_EVENTS = False`. It re-pulls 8-K text from Massive but has no satellite step, so
  every new event is `unknown` and nothing trades.

If you want to test a window past 2026-09, we are happy to regenerate the event file for that window
and send it over.
