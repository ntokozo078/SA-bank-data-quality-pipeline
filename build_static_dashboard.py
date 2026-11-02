#!/usr/bin/env python3
"""
Builds index.html, the static results page hosted on Render, from the output
of the latest main.py run. Every number on the page is read from those files.

Usage
-----
    python main.py
    python build_static_dashboard.py
"""
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from deltalake import DeltaTable

AUDIT_PATH      = "reports/audit_log.json"
SILVER_PATH     = "data/clean/sa_bank_silver"
QUARANTINE_PATH = "data/quarantine/quarantined_batch.csv"
SAMPLE_ROWS     = 10

REASON_LABELS = {
    "invalid_sa_id":       "Invalid SA ID",
    "invalid_cellphone":   "Invalid cellphone",
    "invalid_email":       "Invalid email",
    "missing_name":        "Missing name",
    "anomalous_amount":    "Anomalous amount",
    "amount_out_of_range": "Amount out of range",
}

CSS = """
:root {
  --bg: #ffffff; --text: #1f2328; --muted: #59636e; --line: #d1d9e0;
  --bar: #2f6f8f; --panel: #f6f8fa;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15191e; --text: #e6e9ed; --muted: #9aa4af; --line: #343b43;
    --bar: #6fa8c7; --panel: #1d2228;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 36px 0 10px; }
p { margin: 0 0 10px; }
a { color: var(--bar); }
.muted { color: var(--muted); }
.note { background: var(--panel); border: 1px solid var(--line); padding: 12px 14px; margin: 18px 0; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
.stat { border: 1px solid var(--line); padding: 12px 14px; }
.stat b { display: block; font-size: 1.4rem; font-variant-numeric: tabular-nums; }
.stat span { color: var(--muted); font-size: 0.875rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.925rem; }
th, td { text-align: left; padding: 7px 10px 7px 0; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; padding-right: 0; }
td.bar { width: 45%; padding-right: 14px; }
td.bar div { background: var(--bar); height: 10px; }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 0.875rem; }
"""


def load_outputs() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    with open(AUDIT_PATH) as f:
        audit = json.load(f)
    silver = DeltaTable(SILVER_PATH).to_pandas()
    quarantine = pd.read_csv(QUARANTINE_PATH)
    return audit, silver, quarantine


def bar_table(heading: str, counts: dict[str, int]) -> str:
    """Render label and count rows, with each bar scaled to the largest count."""
    largest = max(counts.values(), default=0) or 1
    rows = "".join(
        f'<tr><td>{html.escape(label)}</td>'
        f'<td class="bar"><div style="width:{n / largest * 100:.1f}%"></div></td>'
        f'<td class="num">{n:,}</td></tr>'
        for label, n in counts.items()
    )
    return (
        f'<div class="scroll"><table><tr><th>{html.escape(heading)}</th><th></th>'
        f'<th class="num">Records</th></tr>{rows}</table></div>'
    )


def risk_bands(scores: pd.Series) -> dict[str, int]:
    bands = pd.cut(
        scores,
        bins=[-1, 9, 19, 39, 59, 100],
        labels=["0-9", "10-19", "20-39", "40-59 (flagged)", "60-100 (flagged)"],
    )
    return {str(label): int(n) for label, n in bands.value_counts().sort_index().items()}


def sample_rows(quarantine: pd.DataFrame) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(str(r.transaction_id))}</td>"
        f'<td class="num">R{r.amount:,.2f}</td>'
        f"<td>{html.escape(r.quarantine_reason.replace('; ', ', '))}</td></tr>"
        for r in quarantine.head(SAMPLE_ROWS).itertuples()
    )
    return (
        '<div class="scroll"><table><tr><th>Transaction</th><th class="num">Amount</th>'
        f"<th>Reason</th></tr>{rows}</table></div>"
    )


def build_page(audit: dict, silver: pd.DataFrame, quarantine: pd.DataFrame) -> str:
    raw, bad, clean = audit["raw_records"], audit["bad_records"], audit["clean_records"]
    rate = audit["failure_rate_percent"]
    limit = audit["circuit_breaker_config"]["max_failure_rate"]
    dq = audit["dq_checks"]
    fraud = audit["fraud_scoring"]
    run_date = datetime.fromisoformat(audit["pipeline_run_time"])

    in_quarantine = int(quarantine["fraud_flag"].sum())
    in_silver = int(silver["fraud_flag"].sum())
    reasons = {REASON_LABELS.get(k, k): v for k, v in audit["quarantine_reasons"].items()}
    scores = pd.concat([silver["fraud_risk_score"], quarantine["fraud_risk_score"]])

    stats = "".join(
        f'<div class="stat"><b>{value}</b><span>{label}</span></div>'
        for value, label in [
            (f"{raw:,}", "records in the batch"),
            (f"{clean:,}", "written to Silver"),
            (f"{bad:,}", f"quarantined ({rate:.1f}%)"),
            (f"{fraud['fraud_flagged']:,}", "fraud flags"),
            (audit["circuit_breaker_state"], f"circuit, rejects above {limit:g}%"),
        ]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SA Bank Data Quality</title>
<meta name="description" content="Results from one run of a Python data quality pipeline on synthetic South African bank data.">
<style>{CSS}</style>
</head>
<body>
<main>
<h1>SA Bank Data Quality Pipeline</h1>
<p class="muted">Results from one batch run on {run_date.day} {run_date:%B %Y}</p>

<p class="note"><b>This data is synthetic.</b> The pipeline generates these transactions and
adds errors on purpose, so the high quarantine rate is expected. This page is a static
snapshot of one run and doesn't update on its own.</p>

<div class="stats">{stats}</div>

<h2>Why records were quarantined</h2>
{bar_table("Failed check", reasons)}
<p class="muted">A record can fail more than one check, so these add up to more than {bad:,}.</p>

<h2>Fraud risk scores</h2>
{bar_table("Score", risk_bands(scores))}
<p class="muted">Eight rules add up to a score, and 40 or more sets a flag. Of the
{fraud['fraud_flagged']:,} flags, {in_quarantine:,} are in quarantine and {in_silver:,} are in Silver.
The rules haven't been tested on real labelled transactions.</p>

<h2>Checks on accepted records</h2>
<p>Great Expectations passed {dq['passed_expectations']} of {dq['total_expectations']} checks
({dq['quality_score']:.1f}%). The failed check is transaction ID uniqueness, because
duplicate IDs aren't quarantined yet.</p>

<h2>Sample of quarantined records</h2>
{sample_rows(quarantine)}
<p class="muted">Customer fields are masked in the quarantine file and left out of this table.</p>

<footer>
<a href="https://github.com/ntokozo078/SA-bank-data-quality-pipeline">Code and full write-up on GitHub</a><br>
Ntokozo Ntombela &middot;
<a href="https://www.linkedin.com/in/ntokozo-ntombela-ba662235a">LinkedIn</a> &middot;
<a href="https://data-engineering-portfolio-xi.vercel.app/">Portfolio</a>
</footer>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the static results page")
    parser.add_argument("--output", default="index.html", help="Where to write the page")
    args = parser.parse_args()

    page = build_page(*load_outputs())
    Path(args.output).write_text(page, encoding="utf-8")
    print(f"Static dashboard written to {args.output}")
