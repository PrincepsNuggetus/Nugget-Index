"""Print the salt for one day's record fingerprints, so that day can be checked publicly.

    NUGGET_DATA_KEY=... python scripts/reveal_salt.py 2026-10-19

Run it on your own computer. The salt reveals nothing about other days, but together with the decrypted
statement it lets anyone recompute that day's equity_sha256, trades_sha256 and flows_sha256.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build, vault  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/reveal_salt.py YYYY-MM-DD")
    pw = vault.passphrase()
    if not pw:
        raise SystemExit("Set NUGGET_DATA_KEY first.")
    print(build.record_salt(pw, sys.argv[1]))
