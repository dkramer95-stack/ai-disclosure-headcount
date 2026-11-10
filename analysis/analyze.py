#!/usr/bin/env python3
"""Analysis for "The Disclosure Gap".

Reads data/extracted.csv (S&P 500 10-Ks, FY2021-FY2026), builds a firm-year
panel, writes analysis/stats.json, regenerates figures/fig1..fig4.svg, and
prints a summary.

Runs on the bundled runtime (pandas + numpy):
  /Users/derek/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3 analysis/analyze.py
"""
from __future__ import annotations

import json
import os
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "extracted.csv")
RANDOM_CUTOFF = "2026-11-10T11:56"  # only filings downloaded after the seeded shuffle
OUT = os.path.join(ROOT, "analysis", "stats.json")
FIGS = os.path.join(ROOT, "figures")

GREEN, RED, GREY, BLUE, ORANGE = "#1F4E79", "#C00000", "#7F7F7F", "#2E75B6", "#C55A11"


def load() -> pd.DataFrame:
    df = pd.read_csv(DATA, dtype={"cik": str, "fiscal_year": "Int64"})
    df = df.dropna(subset=["fiscal_year"])
    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df["employees"] = pd.to_numeric(df["employees"], errors="coerce")
    df["ai_total"] = pd.to_numeric(df["ai_total"], errors="coerce").fillna(0)
    df["workforce_ai"] = pd.to_numeric(df["workforce_ai"], errors="coerce").fillna(0)
    df["words"] = pd.to_numeric(df["words"], errors="coerce")
    df = df.sort_values(["cik", "fiscal_year", "filing_date"])
    df = df.drop_duplicates(["cik", "fiscal_year"], keep="last")
    df = df[(df["words"] > 4000) & df["employees"].notna() & (df["employees"] > 100)]
    if RANDOM_CUTOFF and "file_mtime" in df.columns:
        df = df[df["file_mtime"] >= RANDOM_CUTOFF]
    df["log_emp"] = np.log(df["employees"])
    df["ai_per_1k"] = 1000 * df["ai_total"] / df["words"]
    df["wf_per_1k"] = 1000 * df["workforce_ai"] / df["words"]
    df = df.sort_values(["cik", "fiscal_year"])
    df["growth_next"] = df.groupby("cik")["log_emp"].shift(-1) - df["log_emp"]
    df["growth_prev"] = df["log_emp"] - df.groupby("cik")["log_emp"].shift(1)
    df["big_move"] = (df["growth_next"].abs() > 0.15) | (df["growth_prev"].abs() > 0.15)
    return df


def ols(y, X, names):
    """OLS with HC1 standard errors. Returns dict of coefficients."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    mask = np.isfinite(y) & np.isfinite(X).all(axis=1)
    X, y = X[mask], y[mask]
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    XtX_inv = np.linalg.pinv(X.T @ X)
    meat = (X * resid[:, None] ** 2).T @ X
    cov = XtX_inv @ meat @ XtX_inv * (n / max(n - k, 1))
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else float("nan")
    return {"n": int(n), "r2": round(float(r2), 3),
            "coef": {names[i]: {"b": round(float(beta[i]), 4), "se": round(float(se[i]), 4),
                                "t": round(float(beta[i] / se[i]), 2) if se[i] else None}
                     for i in range(len(names))}}


def design(df, ai_col, fe=True):
    y = df["growth_next"].to_numpy()
    cols = [np.ones(len(df)), df[ai_col].to_numpy(), df["log_emp"].to_numpy()]
    names = ["intercept", ai_col, "log_emp"]
    if fe:
        for col, label in (("fiscal_year", "year"), ("sector", "sector")):
            dummies = pd.get_dummies(df[col], drop_first=True, dtype=float)
            cols.append(dummies.to_numpy())
            names += [f"{label}:{c}" for c in dummies.columns]
    return y, np.column_stack(cols), names


def svg_lines(path, title, series, ylabel, width=900, height=340, zero=None,
              yfmt="{:.0f}", marker=None):
    pad_l, pad_r, pad_t, pad_b = 58, 220, 34, 34
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    ys = [v for _, pts, _ in series for _, v in pts if v is not None]
    y0, y1 = min(ys), max(ys)
    span = (y1 - y0) or 1.0
    y0 -= span * 0.1
    y1 += span * 0.1
    xs = sorted({x for _, pts, _ in series for x, _ in pts})
    pos = {x: i for i, x in enumerate(xs)}

    def X(x):
        return pad_l + pos[x] / max(len(xs) - 1, 1) * iw

    def Y(v):
        return pad_t + ih - (v - y0) / (y1 - y0) * ih

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
         f'font-family="Helvetica,Arial,sans-serif" role="img" aria-label="{escape(title)}">',
         f'<rect width="{width}" height="{height}" fill="#fff"/>',
         f'<text x="{pad_l}" y="20" font-size="14" font-weight="700">{escape(title)}</text>']
    for k in range(5):
        v = y0 + (y1 - y0) * k / 4
        o.append(f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{pad_l+iw}" y2="{Y(v):.1f}" stroke="#E6E6E6"/>')
        o.append(f'<text x="{pad_l-8}" y="{Y(v)+4:.1f}" font-size="10" fill="#666" text-anchor="end">{yfmt.format(v)}</text>')
    if zero is not None and y0 <= zero <= y1:
        o.append(f'<line x1="{pad_l}" y1="{Y(zero):.1f}" x2="{pad_l+iw}" y2="{Y(zero):.1f}" stroke="#999" stroke-dasharray="3,3"/>')
    if marker is not None and marker in pos:
        o.append(f'<line x1="{X(marker):.1f}" y1="{pad_t}" x2="{X(marker):.1f}" y2="{pad_t+ih}" stroke="{RED}" stroke-dasharray="4,3"/>')
    step = max(1, len(xs) // 8)
    for x in xs[::step]:
        o.append(f'<text x="{X(x):.1f}" y="{pad_t+ih+16}" font-size="9" fill="#666" text-anchor="middle">{escape(str(x))}</text>')
    for label, pts, colour in series:
        pts = [(x, y) for x, y in pts if y is not None]
        if not pts:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))
        o.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2"/>')
    for i, (label, _, colour) in enumerate(series):
        yy = pad_t + 6 + i * 16
        o.append(f'<line x1="{pad_l+iw+14}" y1="{yy}" x2="{pad_l+iw+34}" y2="{yy}" stroke="{colour}" stroke-width="2"/>')
        o.append(f'<text x="{pad_l+iw+38}" y="{yy+3}" font-size="10" fill="#333">{escape(label)}</text>')
    o.append(f'<text x="{pad_l}" y="{height-6}" font-size="9" fill="#666">{escape(ylabel)}</text>')
    o.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(o))


def main():
    df = load()
    os.makedirs(FIGS, exist_ok=True)
    stats = {"panel": {"filings": int(len(df)), "firms": int(df["cik"].nunique()),
                       "years": sorted(df["fiscal_year"].unique().tolist())}}

    # disclosure prevalence
    by_year = df.groupby("fiscal_year").agg(
        firms=("cik", "nunique"),
        any_ai=("ai_total", lambda s: (s > 0).sum()),
        any_wf=("workforce_ai", lambda s: (s > 0).sum()),
        mean_ai=("ai_per_1k", "mean"),
        median_ai=("ai_per_1k", "median"),
        mean_wf=("wf_per_1k", "mean"),
    ).round(3)
    stats["by_year"] = by_year.reset_index().to_dict("records")
    stats["share_any_ai"] = (by_year["any_ai"] / by_year["firms"]).round(3).to_dict()

    # sector intensity, latest year
    last = df["fiscal_year"].max()
    sec = (df[df["fiscal_year"] == last].groupby("sector")
           .agg(firms=("cik", "nunique"), mean_ai=("ai_per_1k", "mean"),
                share_ai=("ai_total", lambda s: (s > 0).mean()),
                mean_growth=("growth_next", "mean"))
           .sort_values("mean_ai", ascending=False).round(3))
    stats["latest_year"] = int(last)
    stats["by_sector"] = sec.reset_index().to_dict("records")

    # regressions
    sub = df.dropna(subset=["growth_next"]).copy()
    sub = sub[~sub["big_move"].astype(bool)]
    reg = {}
    for ai_col in ("ai_per_1k", "wf_per_1k"):
        for fe in (False, True):
            y, X, names = design(sub, ai_col, fe=fe)
            reg[f"{ai_col}_fe{int(fe)}"] = ols(y, X, names)
    stats["regressions"] = reg

    # descriptive growth by disclosure group
    sub["ai_group"] = pd.cut(sub["ai_per_1k"], [-0.01, 0.001, 5, 15, 1000],
                             labels=["none", "low", "mid", "high"])
    grp = sub.groupby("ai_group", observed=True).agg(
        n=("growth_next", "size"), mean_growth=("growth_next", "mean"),
        median_growth=("growth_next", "median")).round(4)
    stats["growth_by_group"] = grp.reset_index().astype({"ai_group": str}).to_dict("records")

    # event study: first year with >=3 AI mentions
    ev = df[df["ai_total"] >= 3].sort_values(["cik", "fiscal_year"])
    fmap = ev.drop_duplicates("cik").set_index("cik")["fiscal_year"]
    df["first_ai"] = df["cik"].map(fmap)
    df["event_time"] = df["fiscal_year"] - df["first_ai"]
    es = (df.dropna(subset=["event_time", "growth_next"])
          .groupby("event_time").agg(n=("growth_next", "size"),
                                     mean_growth=("growth_next", "mean")).round(4))
    stats["event_study"] = es.reset_index().to_dict("records")

    with open(OUT, "w") as fh:
        json.dump(stats, fh, indent=2, default=str)

    # figures
    yrs = by_year.index.tolist()
    svg_lines(os.path.join(FIGS, "fig1_prevalence.svg"),
              "Share of S&P 500 10-Ks mentioning AI, by fiscal year",
              [("Any AI term", [(y, 100 * by_year.loc[y, "any_ai"] / by_year.loc[y, "firms"])
                                for y in yrs], GREEN),
               ("AI near workforce language", [(y, 100 * by_year.loc[y, "any_wf"] / by_year.loc[y, "firms"])
                                               for y in yrs], RED)],
              "Per cent of firms filing. Source: SEC 10-K filings.", yfmt="{:.0f}")
    svg_lines(os.path.join(FIGS, "fig2_intensity.svg"),
              "AI mentions per 1,000 words, median and mean",
              [("Mean", [(y, by_year.loc[y, "mean_ai"]) for y in yrs], BLUE),
               ("Median", [(y, by_year.loc[y, "median_ai"]) for y in yrs], GREY)],
              "Term counts divided by document length.", yfmt="{:.1f}")
    svg_lines(os.path.join(FIGS, "fig3_event.svg"),
              "Headcount growth around first AI disclosure",
              [("Mean growth", [(int(r["event_time"]), 100 * r["mean_growth"])
                                for r in stats["event_study"] if -2 <= int(r["event_time"]) <= 3], GREEN)],
              "Log change in employees, per cent. Event 0 = first 10-K with 3+ AI mentions.",
              zero=0, yfmt="{:.0f}")
    svg_lines(os.path.join(FIGS, "fig4_groups.svg"),
              "Next-year headcount growth by AI disclosure intensity",
              [("Mean growth", [(str(r["ai_group"]), 100 * r["mean_growth"])
                                for r in stats["growth_by_group"]], ORANGE)],
              "Log change in employees, per cent, excluding moves above 15 per cent.",
              zero=0, yfmt="{:.0f}")

    print(json.dumps(stats, indent=2, default=str)[:4000])
    print("\nstats:", OUT)
    print("figures:", ", ".join(sorted(os.listdir(FIGS))))


if __name__ == "__main__":
    main()
