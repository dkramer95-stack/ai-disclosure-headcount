#!/usr/bin/env python3
"""Fetch S&P 500 10-K documents without the data.sec.gov submissions API.

Used when data.sec.gov is rate-limiting. Enumerates 10-K filings from the
www.sec.gov browse-edgar listing, resolves the primary document through the
filing index, and downloads it. Filing period is parsed later from the document
text by pipeline/extract.py.

Resumable via data/filings_meta.csv.

Run: python3 pipeline/fetch_html.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FILINGS = os.path.join(DATA, "filings")
UA = {"User-Agent": "Derek Kramer research dkramer95@gmail.com"}
WORKERS = 6
CUTOFF = "2021-01-01"
MAX_FILINGS = 6
lock = threading.Lock()


def get(url, retries=4):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as fh:
                return fh.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if attempt == retries - 1:
                return None
            time.sleep(1.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                return None
            time.sleep(1.0 * (attempt + 1))
    return None


def main():
    with open(os.path.join(DATA, "universe.csv"), newline="") as fh:
        universe = list(csv.DictReader(fh))
    meta_path = os.path.join(DATA, "filings_meta.csv")
    done = set()
    if os.path.exists(meta_path):
        with open(meta_path, newline="") as fh:
            done = {r["accession"] for r in csv.DictReader(fh)}
    print(f"{len(universe)} firms, {len(done)} filings already present", flush=True)

    # Phase A: list 10-K accessions per firm
    queue = []
    for i, r in enumerate(universe):
        cik = int(r["cik"])
        url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
               f"&type=10-K&dateb=&owner=include&count=10")
        html = get(url)
        if html is None:
            print(f"SKIP {r['ticker']}: listing failed", flush=True)
            continue
        text = html.decode("utf-8", "ignore")
        items = re.findall(
            r"(\d{4}-\d{2}-\d{2})</td>\s*<td[^>]*>\s*<a[^>]+href=\"(/Archives/edgar/data/\d+/(\d{18})/)",
            text)
        picked = 0
        for filed, path, acc in items:
            if filed < CUTOFF:
                continue
            if acc in done:
                continue
            queue.append({"ticker": r["ticker"], "company": r["company"],
                          "sector": r["sector"], "cik": cik, "accession_id": acc,
                          "filing_date": filed})
            picked += 1
            if picked >= MAX_FILINGS:
                break
        time.sleep(0.25)
        if (i + 1) % 100 == 0:
            print(f"listed {i+1}/{len(universe)}, {len(queue)} to fetch", flush=True)
    print(f"phase A: {len(queue)} filings to fetch", flush=True)

    # Phase B: resolve primary document and download
    out = open(meta_path, "a", newline="")
    w = csv.writer(out)
    if not done:
        w.writerow(["ticker", "company", "sector", "cik", "accession", "filing_date",
                    "period", "primary_document", "path", "bytes"])

    def fetch(item):
        acc = item["accession_id"]
        idx = get(f"https://www.sec.gov/Archives/edgar/data/{item['cik']}/{acc}/index.json")
        if idx is None:
            raise RuntimeError("index failed")
        data = json.loads(idx)
        docs = [d for d in data["directory"]["item"] if d["name"].endswith(".htm")]
        if not docs:
            raise RuntimeError("no htm")
        doc = max(docs, key=lambda d: int(d["size"]) if d["size"].isdigit() else 0)["name"]
        body = get(f"https://www.sec.gov/Archives/edgar/data/{item['cik']}/{acc}/{doc}")
        if body is None:
            raise RuntimeError("document failed")
        folder = os.path.join(FILINGS, str(item["cik"]))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, acc + ".htm")
        with open(path, "wb") as fh:
            fh.write(body)
        accession = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
        time.sleep(0.1)
        return {"ticker": item["ticker"], "company": item["company"],
                "sector": item["sector"], "cik": item["cik"], "accession": accession,
                "filing_date": item["filing_date"], "period": "",
                "primary_document": doc, "path": os.path.relpath(path, ROOT),
                "bytes": len(body)}

    ok, fail = 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch, it): it for it in queue}
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                row = fut.result()
            except Exception:  # noqa: BLE001
                fail += 1
                continue
            with lock:
                w.writerow([row[k] for k in ("ticker", "company", "sector", "cik",
                                             "accession", "filing_date", "period",
                                             "primary_document", "path", "bytes")])
                ok += 1
                if ok % 250 == 0:
                    out.flush()
                    print(f"downloaded {ok}/{len(queue)}", flush=True)
    out.close()
    print(f"done: {ok} downloaded, {fail} failures", flush=True)


if __name__ == "__main__":
    main()
