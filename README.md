# Nugget Index

A live track record built from every trade in one Interactive Brokers account, published as a web page and backed by records anyone can verify.

**Page:** https://princepsnuggetus.github.io/Nugget-Index/

## What the page shows

- Index value since inception (base 1,000) against the S&P 500 (SPY, total return)
- Holdings and weights, a holdings heatmap, monthly and daily returns
- Every trade with its date, side, price, size as % of NAV and realized result
- Risk and trading statistics, and the proof behind every record

Dollar amounts are never published, only percentages.

## How the index is calculated

The index is **time-weighted**: deposits and withdrawals buy or redeem units at the previous close, so adding or removing money never moves the index. Daily account equity comes directly from IBKR's "Net Asset Value in Base" report.

`index value = account equity / units outstanding`, starting at 1,000 on the first day.

## How it stays honest

1. **Account lock.** `data/records/genesis.json` declares, on day one, that every trade, deposit and withdrawal in this account counts. Its fingerprint is timestamped, so the start date cannot be moved later and losing accounts cannot be swapped out.
2. **Immutable daily records.** Each daily run writes a new file in `data/records/` holding SHA-256 fingerprints of that day's equity, trades, cash flows and the raw IBKR report. Records are never edited or deleted.
3. **Bitcoin timestamps.** Every record is stamped with [OpenTimestamps](https://opentimestamps.org); the `.ots` file next to it proves the record existed before a specific Bitcoin block.
4. **Encrypted source data.** The raw IBKR reports and the ledger are stored encrypted (`*.enc`). Their fingerprints are already in the timestamped records, so when they are revealed, anyone can check they were not altered.

### Verify a record yourself

```bash
pip install opentimestamps-client
ots verify data/records/2026-10-09.json.ots   # needs a Bitcoin node, or use https://opentimestamps.org to upload both files
```

## Repository layout

| Path | What it is |
| --- | --- |
| `site/` | The web page (`index.html`) and its data (`data.json`) |
| `data/records/` | Immutable records and their `.ots` timestamp proofs |
| `data/ledger.json.enc` | Encrypted ledger: daily equity, trades, cash flows, positions |
| `data/raw/` | Encrypted raw IBKR Flex reports |
| `data/notes.json` | Optional public notes on trades, keyed `"YYYY-MM-DD TICKER"` |
| `scripts/` | `flex.py` (IBKR), `build.py` (index math), `vault.py` (encryption), `run_daily.py` (the daily job) |
| `.github/workflows/daily.yml` | Runs every weekday night and deploys the page |
| `config.json` | Name, byline, benchmark, privacy options |

## Disclaimer

This is a personal record, not investment advice. Past performance does not predict future results.
