"""Turn the IBKR ledger into the site's data.json.

NAV method: unitized, time-weighted. The account's daily equity comes straight
from IBKR's "NAV in Base" report. A deposit or withdrawal on day d buys or
redeems units at the previous close's unit value, so cash flows never move the
index. Index value = equity / units, starting at config["base"] (1,000).
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEDGER_FILE = ROOT / "data" / "ledger.json"  # stored as ledger.json.enc when NUGGET_DATA_KEY is set
RECORDS = ROOT / "data" / "records"
META = ROOT / "data" / "meta"
SITE = ROOT / "site"


def load_json(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def save_json(p: Path, obj, pretty=True):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2 if pretty else None, ensure_ascii=False, sort_keys=pretty, separators=None if pretty else (",", ":")) + ("\n" if pretty else ""))


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def config() -> dict:
    return load_json(ROOT / "config.json", {})


# ---------------------------------------------------------------- ledger
EMPTY = {"nav": {}, "flows": [], "trades": [], "corp_actions": [], "positions": {"date": None, "rows": []}, "account": {}}


def load_ledger(pw) -> dict:
    import vault
    raw = vault.read(LEDGER_FILE, pw)
    return {**EMPTY, **json.loads(raw)} if raw else json.loads(json.dumps(EMPTY))


def save_ledger(L: dict, pw) -> Path:
    import vault
    return vault.write(LEDGER_FILE, json.dumps(L, indent=1, sort_keys=True).encode(), pw)


def merge(L: dict, parsed: dict) -> dict:
    """Upsert one parsed Flex report into the ledger. Returns what was new."""
    new_days = sorted(d for d, v in parsed["nav"].items() if d not in L["nav"] and v)
    for d, v in parsed["nav"].items():
        if v or d in L["nav"]:
            L["nav"][d] = v
    L["nav"] = dict(sorted(L["nav"].items()))
    have = {f["id"] for f in L["flows"]}
    nf = [f for f in parsed["flows"] if f["id"] not in have]
    L["flows"] = sorted(L["flows"] + nf, key=lambda f: (f["date"], f["id"]))
    have = {t["id"] for t in L["trades"]}
    nt = [t for t in parsed["trades"] if t["id"] not in have]
    L["trades"] = sorted(L["trades"] + nt, key=lambda t: (t["date"], t.get("time", ""), t["id"]))
    have = {c["id"] for c in L["corp_actions"]}
    L["corp_actions"] = sorted(L["corp_actions"] + [c for c in parsed.get("corp_actions", []) if c["id"] not in have],
                               key=lambda c: (c["date"] or "", c["id"]))
    if parsed["to"] and (L["positions"]["date"] or "") <= parsed["to"]:
        L["positions"] = {"date": parsed["to"], "rows": parsed["positions"]}
    if not L["account"] and parsed.get("account"):
        a = parsed["account"]
        L["account"] = {"masked": a[:1] + "*" * max(0, len(a) - 3) + a[-2:], "first_seen": parsed["to"]}
    return {"days": len(new_days), "flows": len(nf), "trades": len(nt), "new_dates": new_days}


# ---------------------------------------------------------------- inception
def starts_at_first_trade(cfg: dict) -> bool:
    return not cfg.get("inception") and cfg.get("start", "first_trade") == "first_trade"


def inception_date(cfg: dict, L: dict) -> str | None:
    """The day the index is set to 1,000.

    A fixed date in config.json wins. Otherwise (start = "first_trade") it is the close of the
    last funded trading day before the first trade, so the first trade day's result counts.
    If the account was funded the same day as the first trade, it is that day's close.
    """
    if cfg.get("inception"):
        return cfg["inception"]
    funded = sorted(d for d, v in L["nav"].items() if v)
    if not funded:
        return None
    if cfg.get("start", "first_trade") != "first_trade":
        return funded[0]
    if not L["trades"]:
        return None
    first_trade = min(t["date"] for t in L["trades"])
    before = [d for d in funded if d < first_trade]
    return before[-1] if before else first_trade


# ---------------------------------------------------------------- helpers
def benchmark(ticker: str, start: str, end: str) -> pd.Series:
    import yfinance as yf
    s = (pd.Timestamp(start) - timedelta(days=7)).strftime("%Y-%m-%d")
    e = (pd.Timestamp(end) + timedelta(days=3)).strftime("%Y-%m-%d")
    px = yf.download(ticker, start=s, end=e, auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.DataFrame):
        px = px.iloc[:, 0]
    return px.dropna()


def sector_of(tickers: list[str]) -> dict:
    meta = load_json(META / "sectors.json", {})
    missing = [t for t in tickers if t not in meta]
    if missing:
        try:
            import yfinance as yf
            for t in missing:
                try:
                    meta[t] = yf.Ticker(t).info.get("sector") or "Other"
                except Exception:
                    meta[t] = "Other"
        except Exception:
            pass
        save_json(META / "sectors.json", meta)
    return meta


def day_change(tickers: list[str], asof: str) -> dict:
    if not tickers:
        return {}
    try:
        import yfinance as yf
        px = yf.download(tickers, start=(pd.Timestamp(asof) - timedelta(days=10)).strftime("%Y-%m-%d"),
                         end=(pd.Timestamp(asof) + timedelta(days=1)).strftime("%Y-%m-%d"), auto_adjust=True, progress=False)["Close"]
        if isinstance(px, pd.Series):
            px = px.to_frame(tickers[0])
        px = px.loc[:asof].dropna(how="all")
        return {t: float(px[t].iloc[-1] / px[t].iloc[-2] - 1) for t in tickers if t in px and px[t].notna().sum() >= 2}
    except Exception:
        return {}


def label(x: dict) -> str:
    """Display ticker. Options read as 'INSM Nov26 100P'; everything else keeps its symbol."""
    if x.get("asset") == "OPT" and x.get("expiry"):
        k = x.get("strike") or 0
        return f"{x.get('und') or x.get('ticker')} {pd.Timestamp(x['expiry']).strftime('%b%y')} {k:g}{x.get('pc') or ''}"
    return x.get("ticker") or ""


def betas(tickers: list[str], asof: str, bench: str = "SPY") -> dict:
    """One-year beta of weekly returns against the benchmark. Public prices only; empty on failure."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    try:
        import yfinance as yf
        start = (pd.Timestamp(asof) - timedelta(days=380)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(asof) + timedelta(days=1)).strftime("%Y-%m-%d")
        px = yf.download(tickers + [bench], start=start, end=end, auto_adjust=True, progress=False)["Close"]
        if isinstance(px, pd.Series):
            return {}
        wk = px.resample("W-FRI").last().pct_change().iloc[1:]
        out = {}
        for t in tickers:
            if t not in wk:
                continue
            pair = wk[[t, bench]].dropna()
            if len(pair) >= 30 and pair[bench].var() > 0:
                out[t] = round(float(pair[t].cov(pair[bench]) / pair[bench].var()), 2)
        return out
    except Exception:
        return {}


def ots_status(record_path: Path) -> dict:
    """pending | confirmed (with block height) | none"""
    ots = record_path.with_suffix(record_path.suffix + ".ots")
    if not ots.exists():
        return {"status": "none"}
    try:
        info = subprocess.run(["ots", "info", str(ots)], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return {"status": "pending"}
    import re
    m = re.findall(r"BitcoinBlockHeaderAttestation\((\d+)\)", info)
    return {"status": "confirmed", "block": int(min(map(int, m)))} if m else {"status": "pending"}


# ---------------------------------------------------------------- compute
def compute(cfg: dict, L: dict) -> dict | None:
    nav_raw = L["nav"]
    if not nav_raw:
        return None
    flows, trades, positions = L["flows"], L["trades"], L["positions"]
    notes = load_json(ROOT / "data" / "notes.json", {})
    base = float(cfg.get("base", 1000))

    inception = inception_date(cfg, L)
    if not inception:
        return None
    eq = pd.Series(nav_raw, dtype=float)
    eq.index = pd.to_datetime(eq.index)
    eq = eq.sort_index().loc[inception:]
    eq = eq[eq.cumsum() > 0]  # drop leading zero-equity days before the first deposit
    flow_by_day: dict[pd.Timestamp, float] = {}
    for f in flows:
        d = pd.Timestamp(f["date"])
        if d >= eq.index[0]:
            flow_by_day[d] = flow_by_day.get(d, 0.0) + f["amount"]
    # manual corrections for deposits IBKR booked on a different day than the money showed up in equity
    for d, amt in (cfg.get("flow_overrides") or {}).items():
        flow_by_day[pd.Timestamp(d)] = float(amt)

    units, prev_nav, navs = 0.0, base, []
    for d, e in eq.items():
        fl = flow_by_day.get(d, 0.0)
        if units == 0:
            units = e / base  # first day: whatever is in the account defines 1,000
        elif fl:
            units += fl / prev_nav
        nav = e / units
        navs.append(nav)
        prev_nav = nav
    S = pd.DataFrame({"equity": eq, "nav": navs}, index=eq.index)

    bt = cfg.get("benchmark", "SPY")
    try:
        bpx = benchmark(bt, str(S.index[0].date()), str(S.index[-1].date()))
        bpx.index = pd.to_datetime(bpx.index).tz_localize(None)
        b = bpx.reindex(S.index.union(bpx.index)).ffill().reindex(S.index)
        S["spy"] = base * b / b.iloc[0]
    except Exception:
        S["spy"] = base
    try:
        rfp = benchmark("BIL", str(S.index[0].date()), str(S.index[-1].date()))
        rfp.index = pd.to_datetime(rfp.index).tz_localize(None)
        rf_series = rfp.reindex(S.index.union(rfp.index)).ffill().reindex(S.index).pct_change().fillna(0)
    except Exception:
        rf_series = pd.Series(0.0, index=S.index)

    S["ret"] = S.nav.pct_change().fillna(0)
    S["spyret"] = S.spy.pct_change().fillna(0)
    S["dd"] = S.nav / S.nav.cummax() - 1
    S["spydd"] = S.spy / S.spy.cummax() - 1
    # Sanity checks. Issues stop the daily run before anything is written or published.
    # Messages use percentages only: Actions logs of a public repo are public.
    ack = set(cfg.get("acknowledged") or [])
    issues, warnings = [], []
    booked = sum(f["amount"] for f in flows if f["date"] <= inception)
    eq0 = float(S.equity.iloc[0])
    if "inception" not in ack and eq0 > 0 and (eq0 - booked) > max(0.01 * eq0, 50):
        issues.append(f"inception: {(eq0 - booked) / eq0:.0%} of the account's equity on {inception} is not explained by booked deposits "
                      "(an instant credit or a deposit still processing). When that deposit is booked later it would show up as a fake loss.")
    for d in sorted(flow_by_day):
        if d in S.index and d != S.index[0]:
            gap = S.ret.loc[d] - S.spyret.loc[d]
            if abs(gap) > 0.02 and str(d.date()) not in ack:
                issues.append(f"{d.date()}: the index moved {S.ret.loc[d]:+.1%} (benchmark {S.spyret.loc[d]:+.1%}) on a day with a deposit or withdrawal. "
                              "The cash flow and the equity change may have been booked on different days.")

    # trade journal with average-cost P&L and holding days
    lots: dict[str, dict] = {}
    journal = []
    eq_prev = S.equity.shift(1).fillna(S.equity.iloc[0])
    # stock splits change share counts but not cost; apply them in date order alongside trades
    splits = [c for c in L.get("corp_actions", []) if c["type"] in ("FS", "RS") and c["qty"]]
    events = [("T", t["date"], t.get("time", ""), t) for t in trades] + [("S", c["date"], "", c) for c in splits]
    for kind, _, _, t in sorted(events, key=lambda e: (e[1], e[0] == "T", e[2])):
        if kind == "S":
            lot = lots.get(t["ticker"])
            if lot and lot["qty"] > 0 and lot["qty"] + t["qty"] > 0:
                lot["qty"] += t["qty"]
            continue
        d = pd.Timestamp(t["date"])
        if d < S.index[0]:
            continue
        ref_eq = float(eq_prev.asof(d)) if d >= S.index[0] else float(S.equity.iloc[0])
        mult = float(t.get("mult") or 1.0)
        val = t["qty"] * t["price"] * mult
        key = label(t)
        # Lots are signed: positive = long, negative = short (short stock, written options).
        # cost is signed money: paid for longs (+), received for shorts (-), fees always add.
        p = lots.get(key, {"qty": 0.0, "cost": 0.0, "entry": d.toordinal()})
        rec = {"id": t["id"], "date": t["date"], "side": t["side"], "ticker": key, "name": (t.get("name") or "").title(), "price": round(t["price"], 4),
               "pct_nav": round(val / ref_eq, 4) if ref_eq else None, "note": notes.get(str(t["id"])) or notes.get(f'{t["date"]} {t["ticker"]}', "") or notes.get(f'{t["date"]} {key}', "")}
        if t.get("asset") == "OPT":
            rec["asset"] = "OPT"
        if not cfg.get("hide_amounts", True):
            rec.update({"qty": t["qty"], "value": round(val, 2), "fee": round(t["fee"], 2)})
        sign = 1.0 if t["side"] == "BUY" else -1.0
        left = t["qty"]
        if p["qty"] and (p["qty"] > 0) != (sign > 0):  # closes (part of) the open lot
            q = min(left, abs(p["qty"]))
            basis = p["cost"] * q / abs(p["qty"])
            fee_part = t["fee"] * q / t["qty"] if t["qty"] else t["fee"]
            pnl = -sign * q * t["price"] * mult - fee_part - basis
            rec.update({"pnl_pct": round(pnl / abs(basis), 4) if basis else None, "hold_days": int(round(d.toordinal() - p["entry"]))})
            rec["_pnl"] = pnl
            p["cost"] -= basis
            p["qty"] += sign * q
            left -= q
            if abs(p["qty"]) < 1e-9:
                p = {"qty": 0.0, "cost": 0.0, "entry": d.toordinal()}
        if left > 1e-9:  # opens or adds; a trade that flips the position opens the rest the other way
            fee_part = t["fee"] * left / t["qty"] if t["qty"] else t["fee"]
            if p["qty"]:
                p["entry"] = (p["entry"] * abs(p["qty"]) + d.toordinal() * left) / (abs(p["qty"]) + left)
            else:
                p["entry"] = d.toordinal()
            p["qty"] += sign * left
            p["cost"] += sign * left * t["price"] * mult + fee_part
        lots[key] = p
        rec["sha256"] = sha({k: v for k, v in rec.items() if not k.startswith("_")})
        journal.append(rec)

    delay = int(cfg.get("trade_delay_days", 0))
    cutoff = (S.index[-1] - timedelta(days=delay)).strftime("%Y-%m-%d")

    r = S.ret.iloc[1:]
    rs = S.spyret.iloc[1:]
    rf = rf_series.iloc[1:]
    yrs = max((S.index[-1] - S.index[0]).days / 365.25, 1 / 365.25)
    ex = r - rf
    down = ex[ex < 0]
    trough = S.dd.idxmin()
    peak = S.nav.loc[:trough].idxmax()
    after = S.nav.loc[trough:]
    rec_d = after[after >= S.nav.loc[peak]]
    sells = [j for j in journal if "_pnl" in j and j.get("pnl_pct") is not None]  # closing trades, long or short
    wins = [j for j in sells if j["_pnl"] > 0]
    losses = [j for j in sells if j["_pnl"] <= 0]
    prev_year = S[S.index.year < S.index[-1].year]

    def ratio(a, b):
        return float(a / b) if b else None

    stats = {
        "total": S.nav.iloc[-1] / base - 1, "spy_total": S.spy.iloc[-1] / base - 1,
        "cagr": (S.nav.iloc[-1] / base) ** (1 / yrs) - 1, "spy_cagr": (S.spy.iloc[-1] / base) ** (1 / yrs) - 1,
        "vol": float(r.std() * math.sqrt(252)) if len(r) > 1 else 0.0,
        "spy_vol": float(rs.std() * math.sqrt(252)) if len(rs) > 1 else 0.0,
        "sharpe": ratio(ex.mean() * math.sqrt(252), ex.std()) if len(ex) > 1 else None,
        "spy_sharpe": ratio((rs - rf).mean() * math.sqrt(252), (rs - rf).std()) if len(rs) > 1 else None,
        "sortino": ratio(ex.mean() * math.sqrt(252), math.sqrt((down ** 2).mean())) if len(down) else None,
        "maxdd": float(S.dd.min()), "spy_maxdd": float(S.spydd.min()),
        "dd_peak": peak.strftime("%Y-%m-%d"), "dd_trough": trough.strftime("%Y-%m-%d"),
        "dd_recovered": rec_d.index[0].strftime("%Y-%m-%d") if len(rec_d) and trough != peak else None,
        "beta": float(np.cov(r, rs)[0, 1] / np.var(rs, ddof=1)) if len(r) > 2 and np.var(rs, ddof=1) else None,
        "corr": float(np.corrcoef(r, rs)[0, 1]) if len(r) > 2 and r.std() and rs.std() else None,
        "best_day": float(r.max()) if len(r) else 0.0, "worst_day": float(r.min()) if len(r) else 0.0,
        "n_trades": len(journal), "n_closed": len(sells),
        "win_rate": ratio(len(wins), len(sells)),
        "avg_win": float(np.mean([j["pnl_pct"] for j in wins])) if wins else None,
        "avg_loss": float(np.mean([j["pnl_pct"] for j in losses])) if losses else None,
        "profit_factor": ratio(sum(j["_pnl"] for j in wins), -sum(j["_pnl"] for j in losses)) if losses else None,
        "avg_hold": float(np.mean([j["hold_days"] for j in sells])) if sells else None,
        "ytd": S.nav.iloc[-1] / (prev_year.nav.iloc[-1] if len(prev_year) else base) - 1,
        "spy_ytd": S.spy.iloc[-1] / (prev_year.spy.iloc[-1] if len(prev_year) else base) - 1,
        "day": float(S.ret.iloc[-1]), "spy_day": float(S.spyret.iloc[-1]),
    }
    stats["calmar"] = ratio(stats["cagr"], -stats["maxdd"]) if stats["maxdd"] < 0 else None

    me = S.nav.resample("ME").last()
    mb = S.spy.resample("ME").last()
    pm = me.shift(1).fillna(base)
    pb = mb.shift(1).fillna(base)
    monthly = [{"y": d.year, "m": d.month, "r": float(me[d] / pm[d] - 1), "spy": float(mb[d] / pb[d] - 1)} for d in me.index]
    annual = []
    for y in sorted(set(S.index.year)):
        cur, pr = S[S.index.year == y], S[S.index.year < y]
        annual.append({"y": int(y), "r": float(cur.nav.iloc[-1] / (pr.nav.iloc[-1] if len(pr) else base) - 1),
                       "spy": float(cur.spy.iloc[-1] / (pr.spy.iloc[-1] if len(pr) else base) - 1)})

    rows = [p for p in positions["rows"] if p["qty"]]
    eq_last = float(S.equity.iloc[-1])
    und_of = {id(p): (p.get("und") or p["ticker"]) if p.get("asset") == "OPT" else p["ticker"] for p in rows}
    unds = sorted(set(und_of.values()))
    sectors = sector_of(unds)
    chg = day_change([p["ticker"] for p in rows if p.get("asset") != "OPT"], str(S.index[-1].date()))
    beta = betas(unds, str(S.index[-1].date()), cfg.get("benchmark", "SPY"))
    holdings = []
    for p in rows:
        key = label(p)
        lot = lots.get(key)
        u = und_of[id(p)]
        h = {"t": key, "name": p["name"].title(), "sector": sectors.get(u, "Other"),
             "w": p["value"] / eq_last if eq_last else 0, "upnl": (p["upnl"] / abs(p["cost"])) if p["cost"] else 0,
             "day": chg.get(p["ticker"], 0.0), "hold": int(S.index[-1].toordinal() - lot["entry"]) if lot else None,
             "dir": "LONG" if p["qty"] > 0 else "SHORT", "asset": p.get("asset", "STK"), "beta": beta.get(u)}
        if p.get("asset") == "OPT":
            # what the contract controls at the strike, as a share of NAV (signed: written puts are negative)
            notional = p["qty"] * float(p.get("mult") or 100) * float(p.get("strike") or 0)
            h.update({"und": u, "pc": p.get("pc"), "k": p.get("strike"), "exp": p.get("expiry"),
                      "notional_w": notional / eq_last if eq_last else 0})
        holdings.append(h)
    holdings.sort(key=lambda h: -abs(h["w"]))
    cash_w = 1 - sum(h["w"] for h in holdings)
    stk = [h for h in holdings if h["asset"] != "OPT"]
    opt = [h for h in holdings if h["asset"] == "OPT"]
    exposure = {
        "long": sum(h["w"] for h in stk if h["w"] > 0), "short": sum(h["w"] for h in stk if h["w"] < 0),
        "net": sum(h["w"] for h in stk),
        "beta_net": sum(h["w"] * h["beta"] for h in stk if h["beta"] is not None),
        "beta_missing": [h["t"] for h in stk if h["beta"] is None],
        "opt_value": sum(h["w"] for h in opt),
        "opt_notional": sum(h["notional_w"] for h in opt),
    }

    for j in journal:
        j.pop("_pnl", None)
    public_journal = [j for j in journal if j["date"] <= cutoff]
    flow_out = [{"date": f["date"], "type": f["type"], "note": "", "sha256": sha({"date": f["date"], "type": f["type"], "id": f["id"]})}
                for f in flows if pd.Timestamp(f["date"]) >= S.index[0]]

    # proof status: each trade inherits the status of the record that covers its day
    proofs = proof_statuses()
    day_record: dict[str, str] = {}
    for rp in sorted(RECORDS.glob("*.json")):
        if rp.name == "genesis.json":
            continue
        for d in load_json(rp, {}).get("covers", []):
            day_record.setdefault(d, rp.name)
    for j in public_journal:
        j["proof"] = proofs.get(day_record.get(j["date"], ""), {"status": "none"})
    genesis = load_json(RECORDS / "genesis.json", None)
    last_day = S.index[-1].strftime("%Y-%m-%d")
    last_rec = day_record.get(last_day)

    return {
        "demo": False,
        "name": cfg.get("name", "Index"), "byline": cfg.get("byline", ""),
        "benchmark_label": cfg.get("benchmark_label", "S&P 500"),
        "asof": last_day, "start": S.index[0].strftime("%Y-%m-%d"),
        "series": [[d.strftime("%Y-%m-%d"), round(n, 2), round(s, 2), round(a, 5), round(b, 5), round(x, 5)]
                   for d, n, s, a, b, x in zip(S.index, S.nav, S.spy, S.dd, S.spydd, S.ret)],
        "stats": stats, "monthly": monthly, "annual": annual, "holdings": holdings, "cash_w": cash_w, "exposure": exposure,
        "journal": public_journal, "flows": flow_out,
        "genesis": ({**genesis, "sha256": file_sha(RECORDS / "genesis.json"), "proof": proofs.get("genesis.json", {"status": "none"})} if genesis else None),
        "snapshot": ({"date": last_day, "file": last_rec, "sha256": file_sha(RECORDS / last_rec), "proof": proofs.get(last_rec, {"status": "none"})} if last_rec else None),
        "trade_delay_days": delay,
        "repo": cfg.get("repo_url", ""),
        "warnings": warnings,
        "issues": issues,
    }


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------- records
def write_records(cfg: dict, L: dict, statement_sha: str | None, new_dates: list[str]) -> list[Path]:
    """Immutable records: a one-time genesis (account lock) and one record per run covering its new days.

    Records hold only fingerprints, so they are safe in a public repo. The data behind each
    fingerprint lives in the (encrypted) ledger and raw IBKR reports.
    """
    RECORDS.mkdir(parents=True, exist_ok=True)
    out = []
    nav_raw, trades, flows, positions, acct = L["nav"], L["trades"], L["flows"], L["positions"], L["account"]
    inception = inception_date(cfg, L)
    if not inception:
        return out
    new_dates = [d for d in new_dates if d >= inception]
    g = RECORDS / "genesis.json"
    if not g.exists():
        first = inception
        rec = {"type": "account_lock", "broker": "Interactive Brokers", "account": acct.get("masked", ""), "date": first,
               "first_trade": min(t["date"] for t in trades) if trades else None,
               "declaration": "Every trade, deposit and withdrawal in this account from this date on is part of the public record",
               "statement_sha256": statement_sha,
               "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        save_json(g, rec)
        out.append(g)
    if new_dates:
        last = max(new_dates)
        p = RECORDS / f"{last}.json"
        n = 2
        while p.exists():  # records are never rewritten
            p = RECORDS / f"{last}-{n}.json"
            n += 1
        rec = {
            "covers": sorted(new_dates),
            "days": {d: {"equity_sha256": sha({"date": d, "equity": nav_raw.get(d)}),
                         "trade_ids": sorted(str(t["id"]) for t in trades if t["date"] == d),
                         "trades_sha256": sha([t for t in trades if t["date"] == d]),
                         "flows_sha256": sha([f for f in flows if f["date"] == d])} for d in sorted(new_dates)},
            "positions_sha256": sha(positions),
            "statement_sha256": statement_sha,
            "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        save_json(p, rec)
        out.append(p)
    return out


# ---------------------------------------------------------------- timestamps
PROOFS = META / "proofs.json"


def proof_statuses() -> dict:
    return load_json(PROOFS, {})


def stamp_all() -> None:
    """Stamp new records in one batch; upgrade pending proofs once Bitcoin has confirmed them."""
    proofs = proof_statuses()
    files = sorted(RECORDS.glob("*.json"))
    fresh = [p for p in files if not p.with_name(p.name + ".ots").exists()]
    if fresh:
        subprocess.run(["ots", "stamp", *map(str, fresh)], capture_output=True, timeout=300)
        for p in fresh:
            if p.with_name(p.name + ".ots").exists():
                proofs[p.name] = {"status": "pending"}
    pending = [p for p in files if proofs.get(p.name, {}).get("status") != "confirmed" and p.with_name(p.name + ".ots").exists()]
    if pending:
        subprocess.run(["ots", "upgrade", *[str(p.with_name(p.name + ".ots")) for p in pending]], capture_output=True, timeout=300)
        for p in pending:
            proofs[p.name] = ots_status(p)
    save_json(PROOFS, proofs)
