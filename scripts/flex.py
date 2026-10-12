"""Fetch and parse IBKR Flex Query reports.

Fetching uses the Flex Web Service (v3): SendRequest returns a reference code,
GetStatement returns the XML once IBKR has generated it. The token only
downloads reports; it cannot trade.

Parsing reads the sections this project needs from the Activity Flex Query:
  - Net Asset Value (NAV) in Base -> <EquitySummaryByReportDateInBase reportDate total>
  - Cash Transactions             -> <CashTransaction type="Deposits/Withdrawals" ...>
  - Trades                        -> <Trade ...>
  - Open Positions                -> <OpenPosition ...>
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


def parse(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    stmt = root.find(".//FlexStatement")
    if stmt is None:
        raise FlexError("No FlexStatement in report")
    out = {
        "account": stmt.get("accountId", ""),
        "from": _date(stmt.get("fromDate")),
        "to": _date(stmt.get("toDate")),
        "generated": stmt.get("whenGenerated", ""),
        "nav": {},
        "flows": [],
        "trades": [],
        "positions": [],
        # which report sections came back (names only, no values) for diagnosing the Flex Query setup
        "sections": sorted({c.tag for c in stmt}),
    }
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
        out["flows"].append({
            "id": e.get("transactionID") or f"{e.get('dateTime')}:{amt}",
            "date": _date(e.get("reportDate") or e.get("dateTime") or e.get("settleDate")),
            "amount": amt,
            "type": "deposit" if amt > 0 else "withdrawal",
        })
    for e in stmt.iter("Trade"):
        if e.get("levelOfDetail", "EXECUTION").upper() not in ("EXECUTION", "ORDER", ""):
            continue
        if e.get("assetCategory", "STK") not in ("STK", "ETF", "FUND"):
            continue
        qty = _f(e.get("quantity"))
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
        })
    for e in stmt.iter("OpenPosition"):
        if e.get("levelOfDetail", "SUMMARY").upper() not in ("SUMMARY", ""):
            continue
        out["positions"].append({
            "ticker": e.get("symbol"),
            "name": e.get("description", ""),
            "qty": _f(e.get("position")),
            "price": _f(e.get("markPrice")),
            "value": _f(e.get("positionValue")),
            "cost": _f(e.get("costBasisMoney")),
            "upnl": _f(e.get("fifoPnlUnrealized")),
            "date": _date(e.get("reportDate")),
        })
    return out


if __name__ == "__main__":
    tok, q = os.environ.get("IBKR_FLEX_TOKEN"), os.environ.get("IBKR_FLEX_QUERY_ID")
    if not tok or not q:
        raise SystemExit("Set IBKR_FLEX_TOKEN and IBKR_FLEX_QUERY_ID")
    data = parse(fetch(tok, q))
    print({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in data.items()})
