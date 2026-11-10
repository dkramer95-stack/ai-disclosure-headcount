#!/usr/bin/env python3
"""Fetch annual revenue for each S&P 500 company from SEC XBRL.

Uses the companyconcept API (small, one concept per company) across the four
revenue tags in common use, keeps 10-K FY entries with a duration near one year,
and writes one revenue observation per company and fiscal-year end.

Output: data/revenue.csv  (cik, period, revenue_usd, concept)

Run: python3 pipeline/revenue.py
"""
from __future__ import annotations

import csv
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Derek Kramer research dkramer95@gmail.com"}
CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]
WORKERS = 8
lock = threading.Lock()


def get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as fh:
                return json.loads(fh.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                return None
            time.sleep(0.7 * (attempt + 1))
    return None


def duration_days(start, end):
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        return (e - s).days
    except (ValueError, TypeError):
        return None


def main():
    with open(os.path.join(DATA, "universe.csv"), newline="") as fh:
        universe = list(csv.DictReader(fh))
    out_path = os.path.join(DATA, "revenue.csv")
    done = set()
    if os.path.exists(out_path):
        with open(out_path, newline="") as fh:
            done = {r["cik"] for r in csv.DictReader(fh)}
    print(f"universe {len(universe)}, already have revenue for {len(done)}", flush=True)

    def fetch(row):
        cik = int(row["CIK"] if "CIK" in row else row["cik"])
        found = {}
        for concept in CONCEPTS:
            data = get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{concept}.json")
            if not data or "units" not in data:
                continue
            for unit in ("USD",):
                for e in data["units"].get(unit, []):
                    if e.get("form") != "10-K" or e.get("fp") != "FY":
                        continue
                    d = duration_days(e.get("start"), e.get("end"))
                    if d is None or not (350 <= d <= 380):
                        continue
                    key = e["end"]
                    # first concept in preference order wins
                    if key not in found:
                        found[key] = (e["val"], concept)
        return cik, found

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch, r): r for r in universe if str(int(r["cik"])) not in done}
        for fut in as_completed(futs):
            try:
                cik, found = fut.result()
            except Exception:  # noqa: BLE001
                continue
            for period, (val, concept) in found.items():
                rows.append({"cik": cik, "period": period,
                             "revenue_usd": val, "concept": concept})

    rows.sort(key=lambda r: (r["cik"], r["period"]))
    mode = "a" if done else "w"
    with open(out_path, mode, newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["cik", "period", "revenue_usd", "concept"])
        if mode == "w":
            w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} revenue observations -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
