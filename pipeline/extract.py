#!/usr/bin/env python3
"""Extract headcount and AI language from the downloaded 10-K documents.

For each filing in data/filings_meta.csv:
  - strip HTML to plain text
  - extract the employee count (multiple patterns, plausible-range filter)
  - count AI terminology and classify matches that sit near workforce language
  - record text length and a content hash

Writes data/extracted.csv, one row per filing.

Run: python3 pipeline/extract.py
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TERMS = {
    "artificial_intelligence": re.compile(r"(?i)artificial\s+intelligence"),
    "ai_token": re.compile(r"\bA\.?I\.?\b"),
    "machine_learning": re.compile(r"(?i)machine\s+learning|\bML\b"),
    "generative": re.compile(r"(?i)generative"),
    "llm": re.compile(r"(?i)large\s+language\s+model|\bLLMs?\b"),
    "chatgpt": re.compile(r"(?i)chat\s?gpt"),
    "agentic": re.compile(r"(?i)agentic"),
}

WORKFORCE = re.compile(
    r"(?i)employee|workforce|headcount|hiring|hire|job|talent|staff|personnel|labou?r")

EMP_PATTERNS = [
    re.compile(r"(?i)(?:had|has|have|employed|employs|employing)[^.]{0,140}?"
               r"([\d]{1,3}(?:,\d{3})+)\s*(?:full[\s-]*time\s*)?(?:equivalent\s*)?"
               r"(?:employees|people|associates|team members)"),
    re.compile(r"(?i)(?:approximately|about|roughly|nearly)?\s*"
               r"([\d]{1,3}(?:,\d{3})+)\s*(?:full[\s-]*time\s*)?(?:equivalent\s*)?"
               r"employees\s*(?:worldwide|globally|as of|in\s+\d{4})"),
    re.compile(r"(?i)([\d]{1,3}(?:,\d{3})+)\s*(?:full[\s-]*time\s*)?(?:equivalent\s*)?"
               r"employees"),
    re.compile(r"(?i)headcount[^.]{0,60}?([\d]{1,3}(?:,\d{3})+)"),
    re.compile(r"(?i)(?:team|workforce)\s+of\s+(?:approximately\s+|about\s+)?"
               r"([\d]{1,3}(?:,\d{3})+)"),
]


def to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def employees(text: str):
    for pat in EMP_PATTERNS:
        for m in pat.finditer(text):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 10 <= n <= 3_000_000:
                return n, pat.pattern
    return None, None


def main():
    meta_path = os.path.join(DATA, "filings_meta.csv")
    with open(meta_path, newline="") as fh:
        meta = list(csv.DictReader(fh))

    out_path = os.path.join(DATA, "extracted.csv")
    fields = ["ticker", "company", "sector", "cik", "accession", "filing_date",
              "period", "fiscal_year", "words", "employees", "employee_pattern",
              "artificial_intelligence", "ai_token", "machine_learning", "generative",
              "llm", "chatgpt", "agentic", "ai_total", "ai_per_1000_words",
              "workforce_ai", "file_mtime", "sha256"]
    done = set()
    if os.path.exists(out_path):
        with open(out_path, newline="") as fh:
            done = {r["accession"] for r in csv.DictReader(fh)}

    mode = "a" if done else "w"
    with open(out_path, mode, newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if mode == "w":
            w.writeheader()
        rows = 0
        for m in meta:
            if m["accession"] in done:
                continue
            path = os.path.join(ROOT, m["path"])
            if not os.path.exists(path):
                continue
            html = open(path, "rb").read().decode("utf-8", "ignore")
            text = to_text(html)
            words = len(text.split())
            counts = {k: len(p.findall(text)) for k, p in TERMS.items()}
            ai_total = sum(counts.values())

            # workforce-adjacent AI mentions
            wf = 0
            for pat in TERMS.values():
                for hit in pat.finditer(text):
                    lo, hi = max(0, hit.start() - 250), min(len(text), hit.end() + 250)
                    if WORKFORCE.search(text[lo:hi]):
                        wf += 1

            emp, pat = employees(text)
            year = (m["period"] or "")[:4]
            w.writerow({
                "ticker": m["ticker"], "company": m["company"], "sector": m["sector"],
                "cik": m["cik"], "accession": m["accession"],
                "filing_date": m["filing_date"], "period": m["period"],
                "fiscal_year": year, "words": words,
                "employees": emp if emp else "", "employee_pattern": (pat or "")[:60],
                "artificial_intelligence": counts["artificial_intelligence"],
                "ai_token": counts["ai_token"],
                "machine_learning": counts["machine_learning"],
                "generative": counts["generative"], "llm": counts["llm"],
                "chatgpt": counts["chatgpt"], "agentic": counts["agentic"],
                "ai_total": ai_total,
                "ai_per_1000_words": round(1000 * ai_total / words, 3) if words else "",
                "workforce_ai": wf,
                "file_mtime": __import__("datetime").datetime.fromtimestamp(
                    os.path.getmtime(path)).isoformat(timespec="seconds"),
                "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
            })
            rows += 1
            if rows % 250 == 0:
                fh.flush()
                print(f"{rows} filings extracted")
    print(f"extracted {rows} new filings -> {out_path}")


if __name__ == "__main__":
    main()
