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

NOTE: We have sent our TypeSafe API key to Christian. Feel free to use that to run the test. 

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


