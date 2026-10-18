# Notes for Claude

This repo publishes the owner's live IBKR track record as a web page. The owner (PrincepsNuggetus) reads Chinese; reply to him in Chinese. The page itself is in English.

## How it works

- `.github/workflows/daily.yml` runs `scripts/run_daily.py` every weekday night (07:23 UTC), commits new data, then deploys `site/` to GitHub Pages.
- `scripts/flex.py` fetches the IBKR Flex report (secrets `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID`) and parses NAV in Base, Cash Transactions, Trades and Open Positions. Stocks, ETFs, funds and options are kept; options carry multiplier, strike, expiry and put/call.
- `scripts/build.py` merges into the ledger, books trades into signed lots (short stock and written options are negative lots, so a cover or buy-back realises the result), computes the unitized time-weighted NAV (base 1,000; flows buy/redeem units at the prior close), benchmark (yfinance SPY), stats, monthly/annual returns, holdings, the trade journal (average-cost P&L), writes records and runs OpenTimestamps.
- `scripts/vault.py` encrypts the ledger and raw reports with `NUGGET_DATA_KEY` (scrypt + AES-256-GCM). The repo is public, so plaintext account data must never be committed.
- `site/index.html` loads `site/data.json`. Until IBKR is connected, `site/data.json` is demo data (`"demo": true`).

## When the record starts

`config.json` → `"start": "first_trade"`: nothing is stored or published while the account is funded but has no trades. On the first run after the first trade, the index is set to 1,000 at the close of the last funded trading day before that trade (`build.inception_date`), the genesis record is written with that date, and daily records begin. A fixed `"inception": "YYYY-MM-DD"` overrides this.

## When the daily run stops

`run_daily.py` checks the numbers before writing anything and exits with an error (GitHub emails the owner) when:

- **inception**: equity on the inception day is not explained by booked deposits, typically IBKR's instant credit or a deposit still processing. Later, when the deposit is booked, it would look like a loss.
- **a flow day**: the index moved more than 2 percentage points away from the benchmark on a day with a deposit or withdrawal.

Nothing is committed on a stopped run. To fix: look at the decrypted ledger (`NUGGET_DATA_KEY` is a secret; ask the owner to run `python scripts/vault.py data/ledger.json` locally, or reason from the error), then either set `"flow_overrides": {"YYYY-MM-DD": amount}` in `config.json` to book the flow on the day the money actually entered equity, or, if the move is real, add the date (or `"inception"`) to `"acknowledged": [...]`. Explain the change to the owner in Chinese before pushing.

## Rules

- **Never edit, rename or delete anything in `data/records/`** (records and `.ots` proofs). New information goes into new records.
- **Never commit plaintext** `data/ledger.json` or `data/raw/*.xml`. `.gitignore` blocks them; keep it that way. `run_daily.py` refuses to store account data when `public_repo` is true and no key is set.
- Never publish dollar amounts or share quantities on the page unless the owner turns off `hide_amounts` in `config.json`.
- You cannot read the secrets. Never ask the owner to paste the IBKR token or the data key into chat; they go into GitHub secrets only.
- Trade notes live in `data/notes.json`, keyed `"YYYY-MM-DD TICKER"` (or IBKR trade ID). Keep them in the owner's words, translated to English if he writes in Chinese, and confirm the translation with him.

## Testing changes

Run the pipeline in a temporary copy so sample output never lands in the repo:

```bash
python tests/test_pipeline.py
```

It processes `tests/sample_flex.xml` (fictional trades, real prices) offline and checks the NAV and that no amounts leak into `site/data.json`.

`python tests/test_options.py` adds a short stock round trip and a written put to the same sample and checks the lots, the open put's exposure if assigned (`notional_w`) and that nothing leaks.

To preview the page, serve `site/` over HTTP (`python -m http.server -d site`); `data.json` is fetched, so `file://` will not work.

## What data.json carries for position management

The owner's Investment vault (on his computer, not in this repo) reads `site/data.json` to size positions. Besides the page data it uses: `holdings[].asset` (STK / OPT), `holdings[].beta` (one-year weekly beta of the stock or the option's underlying against the benchmark, public prices), for options `und`, `pc`, `k`, `exp` and `notional_w` (contracts × multiplier × strike ÷ NAV, signed), and `exposure` (long, short, net, beta-weighted net, option value and option notional, all as shares of NAV). Keep these keys stable. Position-sizing inputs (downside estimates, risk budgets, clusters) stay in the vault and never go into this public repo.

## Design

The page follows the Plutus "picks" layout the owner chose: warm paper background (`--paper #f7f6f1`), white cards, one 760px column, Geist + Geist Mono + Instrument Serif italic for the byline. Portfolio line is solid ink (`--ink #111522`), S&P 500 is solid blue (`--bench #3b7ff2`). Light theme only, on purpose. Charts: TradingView Lightweight Charts 4.2.0 (main chart, percentage scale) and ECharts 5.5.0 (treemap, calendar). Keep library versions pinned. Gains/losses default to green-up with a red-up toggle.
