# src/pipeline_logger.py
"""
Rolling pipeline run logger for SA Bank DQ Pipeline.
Appends each run's audit entry to a JSONL file (one JSON object per line).
The dashboard reads this file to plot DQ score trends over time.
"""
import json
from datetime import datetime
from pathlib import Path

HISTORY_FILE = "reports/pipeline_history.jsonl"


def log_run(audit: dict, history_file: str = HISTORY_FILE):
    """
    Append an audit entry to the rolling pipeline history log.

    Parameters
    ----------
    audit        : dict produced by the pipeline (from audit log)
    history_file : path to the JSONL history file
    """
    path = Path(history_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "logged_at": datetime.now().isoformat(),
        **audit,
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    print(f"Run logged to {history_file}")


def load_history(history_file: str = HISTORY_FILE, max_runs: int = 100) -> list[dict]:
    """
    Load the last N pipeline runs from the JSONL history file.

    Returns
    -------
    List of dicts, ordered oldest -> newest (for charting).
    """
    path = Path(history_file)
    if not path.exists():
        return []

    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return entries[-max_runs:]


def get_trend_dataframe(history_file: str = HISTORY_FILE, max_runs: int = 100):
    """
    Return a pandas DataFrame of pipeline history suitable for plotting.
    Columns: run_time, dq_score, raw_records, bad_records, clean_records, failure_rate_pct
    """
    import pandas as pd
    entries = load_history(history_file, max_runs)
    if not entries:
        return pd.DataFrame()

    rows = []
    for e in entries:
        rows.append({
            "run_time":         e.get("pipeline_run_time") or e.get("run_time") or e.get("logged_at"),
            "dq_score":         100 - float(e.get("failure_rate_percent", e.get("failure_rate_pct", 0))),
            "raw_records":      e.get("raw_records", 0),
            "bad_records":      e.get("bad_records", 0),
            "clean_records":    e.get("clean_records", 0),
            "failure_rate_pct": e.get("failure_rate_percent", e.get("failure_rate_pct", 0)),
            "circuit_state":    e.get("circuit_breaker_state", e.get("circuit_state", "CLOSED")),
        })

    df = pd.DataFrame(rows)
    df["run_time"] = pd.to_datetime(df["run_time"], errors="coerce")
    df = df.sort_values("run_time").reset_index(drop=True)
    return df
