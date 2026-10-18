"""Shorts and options on top of tests/sample_flex.xml, run in a temporary copy of the repo.

    python tests/test_options.py

Adds a short stock round trip (open, then cover at a lower price) and a written put
(sell to open 2, buy back 1, 1 still open) to the sample report, then checks that the
journal books them as short lots, the open put shows up with its exposure if assigned,
and no amounts leak into site/data.json.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = 'accountId="U9990000" currency="USD"'
OPT = 'assetCategory="OPT" symbol="NVDA  261120P00200000" description="NVDA 20NOV26 200 P" underlyingSymbol="NVDA" putCall="P" strike="200" expiry="20261120" multiplier="100"'
TRADES = f"""
<Trade {A} assetCategory="STK" symbol="TSLA" description="TSLA INC" tradeID="2000" tradeDate="20260601" dateTime="20260601;153000" quantity="-10" tradePrice="300" ibCommission="-1.0" buySell="SELL" openCloseIndicator="O" levelOfDetail="EXECUTION"/>
<Trade {A} assetCategory="STK" symbol="TSLA" description="TSLA INC" tradeID="2001" tradeDate="20260701" dateTime="20260701;153000" quantity="10" tradePrice="240" ibCommission="-1.0" buySell="BUY" openCloseIndicator="C" levelOfDetail="EXECUTION"/>
<Trade {A} {OPT} tradeID="2002" tradeDate="20260901" dateTime="20260901;153000" quantity="-2" tradePrice="8.00" ibCommission="-1.30" buySell="SELL" openCloseIndicator="O" levelOfDetail="EXECUTION"/>
<Trade {A} {OPT} tradeID="2003" tradeDate="20261001" dateTime="20261001;153000" quantity="1" tradePrice="4.00" ibCommission="-0.65" buySell="BUY" openCloseIndicator="C" levelOfDetail="EXECUTION"/>
"""
POSITION = f'<OpenPosition {A} {OPT} position="-1" markPrice="3.5000" positionValue="-350.00" costBasisMoney="-799.35" fifoPnlUnrealized="449.35" side="Short" levelOfDetail="SUMMARY" reportDate="20261009"/>\n'

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git"))
    for p in (work / "data" / "records").glob("*"):
        if p.name != ".gitkeep":
            p.unlink()
    for p in [work / "data" / "ledger.json", work / "data" / "ledger.json.enc", work / "data" / "meta" / "proofs.json"]:
        p.unlink(missing_ok=True)
    xml = (ROOT / "tests" / "sample_flex.xml").read_text()
    xml = xml.replace("</Trades><OpenPositions>", TRADES + "</Trades><OpenPositions>", 1)
    xml = xml.replace("</OpenPositions>", POSITION + "</OpenPositions>", 1)
    src = Path(tmp) / "options_flex.xml"
    src.write_text(xml)

    env = {**os.environ, "NUGGET_DATA_KEY": "test-only passphrase"}
    env.pop("IBKR_FLEX_TOKEN", None)
    r = subprocess.run([sys.executable, "scripts/run_daily.py", "--xml", str(src), "--no-stamp"],
                       cwd=work, env=env, capture_output=True, text=True)
    print(r.stdout, r.stderr)
    assert r.returncode == 0, "pipeline failed"
    text = (work / "site" / "data.json").read_text()
    data = json.loads(text)
    j = {x["id"]: x for x in data["journal"]}

    # short stock: opening sell has no result, the cover realises (300*10 - 1) - (240*10 + 1) on a 2,999 basis
    assert "pnl_pct" not in j["2000"], j["2000"]
    assert abs(j["2001"]["pnl_pct"] - (2999 - 2401) / 2999) < 1e-3, j["2001"]
    # written put: 2 contracts at 8.00 x 100, buy back 1 at 4.00
    assert j["2002"]["ticker"] == "NVDA Nov26 200P" and j["2002"]["asset"] == "OPT"
    assert "pnl_pct" not in j["2002"]
    basis = (1600 - 1.30) / 2
    assert abs(j["2003"]["pnl_pct"] - (basis - 400 - 0.65) / basis) < 1e-3, j["2003"]
    assert data["stats"]["n_trades"] == 35 and data["stats"]["n_closed"] >= 2

    put = next(h for h in data["holdings"] if h["t"] == "NVDA Nov26 200P")
    assert put["asset"] == "OPT" and put["dir"] == "SHORT" and put["pc"] == "P" and put["und"] == "NVDA"
    assert put["w"] < 0 and put["upnl"] > 0, put
    eq = 96525.03  # NAV on 2026-10-09 in the sample
    assert abs(put["notional_w"] - (-1 * 100 * 200) / eq) < 1e-6, put
    assert abs(data["exposure"]["opt_notional"] - put["notional_w"]) < 1e-9
    assert all(h.get("asset") in ("STK", "OPT") for h in data["holdings"])

    for leak in ('"qty"', '"equity"', '"value"', '"amount"'):
        assert leak not in text, f"{leak} leaked into public data"
    print("OK: short and option lots, put exposure", round(put["notional_w"], 4), "| no amounts in data.json")
