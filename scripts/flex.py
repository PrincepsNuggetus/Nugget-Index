"""Fetch and parse IBKR Flex Query reports.

Fetching uses the Flex Web Service (v3): SendRequest returns a reference code,
GetStatement returns the XML once IBKR has generated it. The token only
downloads reports; it cannot trade.

Parsing reads the sections this project needs from the Activity Flex Query:
  - Net Asset Value (NAV) in Base -> <EquitySummaryByReportDateInBase reportDate total>
  - Cash Transactions             -> <CashTransaction type="Deposits/Withdrawals" ...>
  - Trades                        -> <Trade ...>          (stocks, ETFs, funds and options)
  - Open Positions                -> <OpenPosition ...>   (options carry multiplier, strike, expiry, putCall)
See docs/SETUP.zh.md for how the query is configured.
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

# Host for the Flex Web Service; override with IBKR_FLEX_BASE if IBKR moves it.
BASE = os.environ.get("IBKR_FLEX_BASE", "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService")
HEADERS = {"User-Agent": "nugget-index/1.0"}


RETRY_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1019", "1021"}


class FlexError(RuntimeError):
    def __init__(self, msg: str, code: str | None = None):
        super().__init__(msg)
        self.code = code


def fetch(token: str, query_id: str, tries: int = 12, wait: int = 10) -> bytes:
    r = requests.get(f"{BASE}/SendRequest", params={"t": token, "q": query_id, "v": 3}, headers=HEADERS, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    if root.findtext("Status") != "Success":
        raise FlexError(f"SendRequest failed: {root.findtext('ErrorCode')} {root.findtext('ErrorMessage')}", root.findtext("ErrorCode"))
    ref = root.findtext("ReferenceCode")
    url = root.findtext("Url") or f"{BASE}/GetStatement"
    for _ in range(tries):
        time.sleep(wait)
        g = requests.get(url, params={"t": token, "q": ref, "v": 3}, headers=HEADERS, timeout=120)
        g.raise_for_status()
        if g.content.lstrip().startswith(b"<FlexQueryResponse"):
            return g.content
        err = ET.fromstring(g.content)
        code = err.findtext("ErrorCode")
        if code in RETRY_CODES:  # generation in progress, server busy, data not ready yet
            continue
        raise FlexError(f"GetStatement failed: {code} {err.findtext('ErrorMessage')}", code)
    raise FlexError("Statement was not ready after waiting; try again later", "timeout")


def _date(s: str | None) -> str | None:
    """IBKR dates come as yyyyMMdd or yyyy-MM-dd, optionally followed by ;HHmmss or a time."""
    if not s:
        return None
    s = s.strip().split(";")[0].split(" ")[0].split(",")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unrecognised IBKR date: {s!r}")


def _f(x, default=0.0) -> float:
    try:
        return float(x) if x not in (None, "") else default
    except ValueError:
        return default


ASSETS = ("STK", "ETF", "FUND", "OPT")


def _contract(e) -> dict:
    """Asset class and, for options, the contract terms (needed for exposure if assigned)."""
    asset = e.get("assetCategory", "STK") or "STK"
    c = {"asset": asset, "mult": _f(e.get("multiplier"), 1.0) or 1.0}
    if asset == "OPT":
        c.update({
            "und": e.get("underlyingSymbol") or (e.get("symbol") or "").split(" ")[0],
            "pc": (e.get("putCall") or "").upper()[:1],
            "strike": _f(e.get("strike")),
            "expiry": _date(e.get("expiry")),
        })
    return c


def parse(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    # With "Breakout by Day" on, IBKR sends one <FlexStatement> per day; read all of them, not just the first.
    stmts = root.findall(".//FlexStatement")
    if not stmts:
        raise FlexError("No FlexStatement in report")
    froms = [d for d in (_date(s.get("fromDate")) for s in stmts) if d]
    tos = [d for d in (_date(s.get("toDate")) for s in stmts) if d]
    out = {
        "account": next((s.get("accountId") for s in stmts if s.get("accountId")), ""),
        "from": min(froms) if froms else None,
        "to": max(tos) if tos else None,
        "generated": stmts[0].get("whenGenerated", ""),
        "statements": len(stmts),
        "nav": {},
        "flows": [],
        "trades": [],
        "positions": [],
        "corp_actions": [],
        # which report sections came back (names only, no values) for diagnosing the Flex Query setup
        "sections": sorted({c.tag for s in stmts for c in s}),
    }
    seen = {"flows": set(), "trades": set(), "corp_actions": set(), "positions": set()}
    for stmt in stmts:
        _parse_statement(stmt, out, seen)
    # With "Breakout by Day" on, Open Positions repeats for every day in the period.
    # Keep only the latest day's rows as the current holdings.
    if out["positions"]:
        latest = max(p["date"] or "" for p in out["positions"])
        out["positions"] = [p for p in out["positions"] if (p["date"] or "") == latest]
    return out


def _parse_statement(stmt, out: dict, seen: dict) -> None:
    for e in stmt.iter("EquitySummaryByReportDateInBase"):
        d = _date(e.get("reportDate"))
        if d:
            out["nav"][d] = _f(e.get("total"))
    for e in stmt.iter("CashTransaction"):
        t = (e.get("type") or "").lower()
        if "deposit" not in t and "withdraw" not in t:
            continue
        if e.get("levelOfDetail", "DETAIL").upper() not in ("DETAIL", ""):
            continue
        amt = _f(e.get("amount"))
        fid = e.get("transactionID") or f"{e.get('dateTime')}:{amt}"
        if fid in seen["flows"]:
            continue
        seen["flows"].add(fid)
        out["flows"].append({
            "id": e.get("transactionID") or f"{e.get('dateTime')}:{amt}",
            "date": _date(e.get("reportDate") or e.get("dateTime") or e.get("settleDate")),
            "amount": amt,
            "type": "deposit" if amt > 0 else "withdrawal",
        })
    for e in stmt.iter("Trade"):
        if e.get("levelOfDetail", "EXECUTION").upper() not in ("EXECUTION", "ORDER", ""):
            continue
        if e.get("assetCategory", "STK") not in ASSETS:
            continue
        qty = _f(e.get("quantity"))
        tid = e.get("tradeID") or e.get("ibExecID") or f"{e.get('dateTime')}:{e.get('symbol')}:{qty}"
        if tid in seen["trades"]:
            continue
        seen["trades"].add(tid)
        out["trades"].append({
            "id": e.get("tradeID") or e.get("ibExecID") or f"{e.get('dateTime')}:{e.get('symbol')}:{qty}",
            "date": _date(e.get("tradeDate") or e.get("dateTime")),
            "time": (e.get("dateTime") or "").replace(";", " "),
            "ticker": e.get("symbol"),
            "name": e.get("description", ""),
            "side": "BUY" if (e.get("buySell") or ("BUY" if qty > 0 else "SELL")).upper().startswith("BUY") else "SELL",
            "qty": abs(qty),
            "price": _f(e.get("tradePrice")),
            "fee": abs(_f(e.get("ibCommission"))) + abs(_f(e.get("taxes"))),
            "currency": e.get("currency", "USD"),
            **_contract(e),
        })
    for e in stmt.iter("CorporateAction"):
        if e.get("levelOfDetail", "DETAIL").upper() not in ("DETAIL", ""):
            continue
        cid = e.get("actionID") or e.get("transactionID") or f"{e.get('reportDate')}:{e.get('symbol')}:{e.get('quantity')}"
        if cid in seen["corp_actions"]:
            continue
        seen["corp_actions"].add(cid)
        out["corp_actions"].append({
            "id": e.get("actionID") or e.get("transactionID") or f"{e.get('reportDate')}:{e.get('symbol')}:{e.get('quantity')}",
            "date": _date(e.get("reportDate") or e.get("dateTime")),
            "ticker": e.get("symbol"),
            "type": (e.get("type") or "").upper(),  # FS = forward split, RS = reverse split, ...
            "qty": _f(e.get("quantity")),
            "description": e.get("description", ""),
        })
    for e in stmt.iter("OpenPosition"):
        if e.get("levelOfDetail", "SUMMARY").upper() not in ("SUMMARY", ""):
            continue
        pkey = (e.get("reportDate"), e.get("conid") or e.get("symbol"), e.get("position"))
        if pkey in seen["positions"]:
            continue
        seen["positions"].add(pkey)
        out["positions"].append({
            "ticker": e.get("symbol"),
            "name": e.get("description", ""),
            "qty": _f(e.get("position")),
            "price": _f(e.get("markPrice")),
            "value": _f(e.get("positionValue")),
            "cost": _f(e.get("costBasisMoney")),
            "upnl": _f(e.get("fifoPnlUnrealized")),
            "date": _date(e.get("reportDate")),
            **_contract(e),
        })


if __name__ == "__main__":
    tok, q = os.environ.get("IBKR_FLEX_TOKEN"), os.environ.get("IBKR_FLEX_QUERY_ID")
    if not tok or not q:
        raise SystemExit("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID")
    data = parse(fetch(tok, q))
    print({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in data.items()})
