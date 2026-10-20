"""Record fingerprints are salted, and days whose record was withdrawn are written again.

    python tests/test_records.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "test-only passphrase"

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / "repo"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git"))
    rec_dir = work / "data" / "records"
    for p in rec_dir.glob("*"):
        if p.name != ".gitkeep":
            p.unlink()
    for p in [work / "data" / "ledger.json", work / "data" / "ledger.json.enc", work / "data" / "meta" / "proofs.json"]:
        p.unlink(missing_ok=True)
    env = {**os.environ, "NUGGET_DATA_KEY": KEY}
    env.pop("IBKR_FLEX_TOKEN", None)
    run = lambda *a: subprocess.run([sys.executable, "scripts/run_daily.py", *a, "--no-stamp"], cwd=work, env=env,
                                    capture_output=True, text=True)
    r = run("--xml", "tests/sample_flex.xml")
    assert r.returncode == 0, r.stdout + r.stderr
    recs = [p for p in rec_dir.glob("*.json") if p.name != "genesis.json"]
    assert len(recs) == 1, recs
    rec = json.loads(recs[0].read_text())
    assert rec["hash_scheme"].startswith("salted-v2")
    sys.path.insert(0, str(work / "scripts"))
    import build  # noqa: E402
    L = build.load_ledger(KEY)
    d = rec["covers"][-1]
    # The unsalted fingerprint of the day's equity must not appear anywhere in the record.
    plain = build.sha({"date": d, "equity": L["nav"][d]})
    assert plain not in recs[0].read_text(), "unsalted equity fingerprint leaked"
    assert rec["days"][d]["equity_sha256"] == build.salted_sha({"date": d, "equity": L["nav"][d]}, build.record_salt(KEY, d))
    # A different key gives different fingerprints, so they cannot be checked without the salt.
    assert build.record_salt(KEY, d) != build.record_salt("other key", d)

    # Withdraw the record, run again with no new report: the same days are written again, salted.
    covers = set(rec["covers"])
    recs[0].unlink()
    r = run()
    assert r.returncode == 0, r.stdout + r.stderr
    again = [json.loads(p.read_text()) for p in rec_dir.glob("*.json") if p.name != "genesis.json"]
    assert len(again) == 1 and set(again[0]["covers"]) == covers, "withdrawn days were not written again"
    assert again[0]["hash_scheme"].startswith("salted-v2")
    print(f"OK: salted record, {len(covers)} days; withdrawn record rewritten with the same days")
