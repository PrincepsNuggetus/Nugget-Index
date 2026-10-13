"""Daily job: pull IBKR → update ledger → write immutable records → timestamp → rebuild site/data.json.

Runs in GitHub Actions (see .github/workflows/daily.yml). Safe to run locally:
  IBKR_FLEX_TOKEN=... IBKR_FLEX_QUERY_ID=... NUGGET_DATA_KEY=... python scripts/run_daily.py
Use --xml path/to/report.xml to process a downloaded Flex report instead of fetching.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import flex  # noqa: E402
import vault  # noqa: E402

ROOT = build.ROOT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", help="process a local Flex XML file instead of fetching")
    ap.add_argument("--no-stamp", action="store_true", help="skip OpenTimestamps (offline runs)")
    args = ap.parse_args()

    cfg = build.config()
    pw = vault.passphrase()
    if cfg.get("public_repo", True) and not pw:
        # Never write account data in the clear to a public repository.
        if args.xml or os.environ.get("IBKR_FLEX_TOKEN"):
            print("NUGGET_DATA_KEY is not set. Refusing to store account data in a public repo.", file=sys.stderr)
            return 1

    raw = None
    if args.xml:
        raw = Path(args.xml).read_bytes()
    elif os.environ.get("IBKR_FLEX_TOKEN") and os.environ.get("IBKR_FLEX_QUERY_ID"):
        try:
            raw = flex.fetch(os.environ["IBKR_FLEX_TOKEN"], os.environ["IBKR_FLEX_QUERY_ID"])
        except flex.FlexError as e:
            # 1003 = statement not available yet (new or unfunded account); "timeout" = IBKR still generating.
            # Anything else (bad token, wrong query ID, expired token) should fail loudly.
            if e.code not in ("1003", "timeout"):
                raise
            print(f"IBKR report not available yet ({e}); will try again next run.")
    else:
        print("No IBKR credentials yet; keeping the demo page.")

    L = build.load_ledger(pw)
    statement_sha = None
    new_dates: list[str] = []
    if raw:
        parsed = flex.parse(raw)
        print("Report sections:", ", ".join(parsed["sections"]) or "none")
        needed = {"EquitySummaryInBase", "CashTransactions", "Trades", "OpenPositions"}
        missing = needed - set(parsed["sections"])
        if missing:
            print("::warning::Flex Query is missing sections: " + ", ".join(sorted(missing)) + ". See docs/SETUP.zh.md step 3a.")
        funded = any(v for v in parsed["nav"].values()) or parsed["trades"] or parsed["flows"]
        if not funded:
            print("Account has no funds or activity yet; nothing to record. The demo page stays up.")
            raw = None
        elif build.starts_at_first_trade(cfg) and not (L["trades"] or parsed["trades"]):
            # Performance starts at the first trade. Until then nothing is stored or published;
            # the 365-day report will still contain these days once the first trade happens.
            print("Account is funded but has no trades yet. The record starts at the first trade; nothing published.")
            raw = None
    if raw:
        statement_sha = hashlib.sha256(raw).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        vault.write(ROOT / "data" / "raw" / f"flex-{parsed['to'] or 'unknown'}-{stamp}.xml", raw, pw)
        res = build.merge(L, parsed)
        new_dates = res["new_dates"]
        print(f"IBKR report {parsed['from']}..{parsed['to']}: +{res['days']} days, +{res['trades']} trades, +{res['flows']} cash flows")
        build.save_ledger(L, pw)

    # Check the numbers before anything is written or published.
    pre = build.compute(cfg, L)
    if pre and pre.get("issues"):
        for i in pre["issues"]:
            print("::error::" + i)
        print("Stopped before writing records or updating the page. See CLAUDE.md, 'When the daily run stops'.")
        return 1

    written = build.write_records(cfg, L, statement_sha, new_dates) if L["nav"] else []
    if written:
        print(f"Wrote {len(written)} record(s)")
    if not args.no_stamp:
        build.stamp_all()

    data = build.compute(cfg, L)
    if data:
        data.pop("issues", None)
        for w in data.pop("warnings", []):
            print("::warning::" + w)
        (ROOT / "site" / "data.json").write_text(json.dumps(data, separators=(",", ":"), default=float))
        print(f"Built site/data.json: NAV {data['series'][-1][1]:.2f} as of {data['asof']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
