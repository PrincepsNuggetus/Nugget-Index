"""End-to-end check on tests/sample_flex.xml, run in a temporary copy of the repo.

    python tests/test_pipeline.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git"))
    for p in (work / "data" / "records").glob("*"):
        if p.name != ".gitkeep":
            p.unlink()
    for p in [work / "data" / "ledger.json", work / "data" / "ledger.json.enc", work / "data" / "meta" / "proofs.json"]:
        p.unlink(missing_ok=True)
    env = {**os.environ, "NUGGET_DATA_KEY": "test-only passphrase"}
    env.pop("IBKR_FLEX_TOKEN", None)
    r = subprocess.run([sys.executable, "scripts/run_daily.py", "--xml", "tests/sample_flex.xml", "--no-stamp"],
                       cwd=work, env=env, capture_output=True, text=True)
    print(r.stdout, r.stderr)
    assert r.returncode == 0, "pipeline failed"
    data = json.loads((work / "site" / "data.json").read_text())
    nav = data["series"][-1][1]
    assert abs(nav - 1739.72) < 0.01, f"unexpected final NAV {nav}"
    assert data["stats"]["n_trades"] == 31
    text = (work / "site" / "data.json").read_text()
    for leak in ('"qty"', '"equity"', '"value"', '"amount"'):
        assert leak not in text, f"{leak} leaked into public data"
    assert (work / "data" / "ledger.json.enc").exists() and not (work / "data" / "ledger.json").exists()
    assert any((work / "data" / "raw").glob("*.xml.enc"))
    print("OK: NAV", nav, "| trades", data["stats"]["n_trades"], "| ledger encrypted, no amounts in data.json")
