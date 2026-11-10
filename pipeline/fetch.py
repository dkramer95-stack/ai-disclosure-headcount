#!/usr/bin/env python3
"""Fetch S&P 500 10-K filings from SEC EDGAR (threaded, resumable).

Phase A: read each company's submissions JSON and list its 10-K filings with a
period of report on or after 2021-01-01 (up to 7 most recent).
Phase B: download the primary documents with a small thread pool.

Resumable: anything already in data/filings_meta.csv is skipped.

Outputs:
    data/universe.csv
    data/filings_meta.csv
    data/filings/<cik>/<accession>.htm

Run: python3 pipeline/fetch.py
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

import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FILINGS = os.path.join(DATA, "filings")
UA = {"User-Agent": os.environ.get(
    "SEC_USER_AGENT", "portfolio-research (set SEC_USER_AGENT)")}
UNIVERSE_URL = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
                "main/data/constituents.csv")
CUTOFF, MAX_FILINGS, WORKERS = "2021-01-01", 6, 8

lock = threading.Lock()


def get(url, retries=5):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as fh:
                return fh.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if attempt == retries - 1:
                raise
            time.sleep(0.8 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError("unreachable")


def main():
    os.makedirs(FILINGS, exist_ok=True)
    raw = get(UNIVERSE_URL).decode("utf-8")
    universe = list(csv.DictReader(raw.splitlines()))
    random.Random(42).shuffle(universe)  # random sample while partial
    with open(os.path.join(DATA, "universe.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "company", "sector", "cik"])
        for r in universe:
            w.writerow([r["Symbol"], r["Security"], r["GICS Sector"], int(r["CIK"])])
    print(f"universe: {len(universe)} companies", flush=True)

    meta_path = os.path.join(DATA, "filings_meta.csv")
    done = set()
    if os.path.exists(meta_path):
        with open(meta_path, newline="") as fh:
            done = {r["accession"] for r in csv.DictReader(fh)}
    print(f"already have: {len(done)} filings", flush=True)

    # Phase A: enumerate
    queue = []
    for i, r in enumerate(universe):
        cik = int(r["CIK"])
        try:
            sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP {r['Symbol']}: {exc}", flush=True)
            continue
        time.sleep(0.55)  # stay under the data.sec.gov rate limit
        rec = sub["filings"]["recent"]
        picks = []
        for j, form in enumerate(rec["form"]):
            if form != "10-K":
                continue
            period = rec["reportDate"][j] or ""
            if period >= CUTOFF:
                picks.append((rec["accessionNumber"][j], rec["filingDate"][j], period,
                              rec["primaryDocument"][j]))
        for accession, filed, period, doc in picks[:MAX_FILINGS]:
            if accession in done:
                continue
            queue.append({"ticker": r["Symbol"], "company": r["Security"],
                          "sector": r["GICS Sector"], "cik": cik,
                          "accession": accession, "filing_date": filed,
                          "period": period, "doc": doc})
        if (i + 1) % 100 == 0:
            print(f"enumerated {i+1}/{len(universe)}, {len(queue)} to download", flush=True)

    print(f"phase A done: {len(queue)} filings to download", flush=True)

    # Phase B: download
    out = open(meta_path, "a", newline="")
    w = csv.writer(out)
    if not done:
        w.writerow(["ticker", "company", "sector", "cik", "accession", "filing_date",
                    "period", "primary_document", "path", "bytes"])

    def fetch(item):
        acc = item["accession"].replace("-", "")
        folder = os.path.join(FILINGS, str(item["cik"]))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, acc + ".htm")
        if not os.path.exists(path):
            body = get(f"https://www.sec.gov/Archives/edgar/data/{item['cik']}/{acc}/{item['doc']}")
            with open(path, "wb") as fh:
                fh.write(body)
            time.sleep(0.05)
        return item, path, os.path.getsize(path)

    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch, it): it for it in queue}
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                item, path, size = fut.result()
            except Exception as exc:  # noqa: BLE001
                fail += 1
                if fail <= 10:
                    print(f"FAIL {it['ticker']} {it['accession']}: {exc}", flush=True)
                continue
            with lock:
                w.writerow([item["ticker"], item["company"], item["sector"], item["cik"],
                            item["accession"], item["filing_date"], item["period"],
                            item["doc"], os.path.relpath(path, ROOT), size])
                ok += 1
                if ok % 250 == 0:
                    out.flush()
                    print(f"downloaded {ok}/{len(queue)}", flush=True)
    out.close()
    print(f"done: {ok} downloaded, {fail} failures", flush=True)


if __name__ == "__main__":
    main()
