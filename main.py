# main.py
#!/usr/bin/env python3
"""
SA Bank Data Quality Pipeline — Main Entry Point
Orchestrates data generation, validation, anomaly detection,
fraud scoring, quarantine, PII masking, and audit logging.

Usage
-----
    python main.py              # default 5,000 records
    python main.py 10000        # custom record count
"""
import pandas as pd
import numpy as np
from datetime import datetime
import os
import json
import sys
from pathlib import Path

# Fix Windows cp1252 terminal encoding so emoji in print() work correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from data_generator import generate_messy_sa_data
from validators import validate_sa_id, validate_cellphone, validate_email, validate_amount
from pii_masker import mask_pii
from circuit_breaker import DataCircuitBreaker
from quality_checks import run_data_quality_checks
from fraud_scorer import score_fraud_risk, fraud_summary
from pipeline_logger import log_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_directories():
    """Create all required directories if they don't exist."""
    for directory in ["data/raw", "data/clean", "data/quarantine", "reports", "logs"]:
        Path(directory).mkdir(parents=True, exist_ok=True)
    print("✅ Directory structure ready")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(n_records: int = 5_000) -> pd.DataFrame | None:
    """Execute the full SA Bank Data Quality Pipeline."""
    print("🚀 Starting SA Bank Data Quality Pipeline...\n")

    # 1. Setup
    ensure_directories()

    # 2. Generate raw messy data
    print("📥 Step 1: Generating realistic messy South African data...")
    df = generate_messy_sa_data(n=n_records)

    # 3. Apply SA-specific validations
    print("\n🔍 Step 2: Running SA-specific quality validations...")
    df["sa_id_valid"]       = df["sa_id"].astype(str).apply(validate_sa_id)
    df["sa_id_valid_flag"]  = df["sa_id_valid"].apply(lambda x: x["valid"])
    df["cellphone_valid"]   = df["cellphone"].apply(validate_cellphone)
    df["email_valid"]       = df["email"].apply(validate_email)
    df["amount_valid"]      = df["amount"].apply(validate_amount)

    # 4. Anomaly detection (ML with graceful statistical fallback)
    print("📊 Step 3: Detecting anomalies in transaction amounts...")
    anomaly_method = "none"
    try:
        from sklearn.ensemble import IsolationForest
        iso = IsolationForest(contamination=0.05, random_state=42, n_jobs=-1)
        df["is_anomaly"] = iso.fit_predict(df[["amount"]]) == -1
        anomaly_method = "IsolationForest"
        print("   ✅ IsolationForest active (ML-based outlier detection)")
    except ImportError:
        print("   ⚠️ scikit-learn unavailable. Falling back to IQR anomaly detection...")
        q1, q3 = df["amount"].quantile([0.25, 0.75])
        iqr = q3 - q1
        df["is_anomaly"] = (df["amount"] < q1 - 3 * iqr) | (df["amount"] > q3 + 3 * iqr)
        anomaly_method = "IQR"
        print("   ✅ IQR fallback active")

    # 5. Fraud risk scoring
    print("\n🔍 Step 4: Scoring fraud risk...")
    df = score_fraud_risk(df)

    # 6. Identify bad records
    bad_mask = (
        ~df["sa_id_valid_flag"]
        | ~df["cellphone_valid"]
        | ~df["email_valid"]
        | df["is_anomaly"]
        | df["customer_name"].isnull()
    )
    bad_records = df[bad_mask].copy()
    clean_df    = df[~bad_mask].copy()

    failure_rate = len(bad_records) / len(df) * 100 if len(df) > 0 else 0
    print(f"\n📉 Detected {len(bad_records):,} bad records out of {len(df):,} ({failure_rate:.1f}%)")

    # 7. Quarantine bad records
    quarantine_path = "data/quarantine/quarantined_batch.csv"
    bad_records.to_csv(quarantine_path, index=False)
    print(f"🚨 Bad records quarantined → {quarantine_path}")

    # 8. Circuit Breaker check
    print("\n⚡ Step 5: Circuit Breaker Evaluation...")
    breaker = DataCircuitBreaker(failure_threshold=5, max_failure_rate=8.0)

    if not breaker.check_batch(len(bad_records), len(df)):
        print("⛔ Pipeline stopped due to high failure rate. Fix upstream data sources.")
        bad_records.to_csv("data/quarantine/bad_batch_circuit_open.csv", index=False)
        return None

    # 9. Light remediation on clean data
    print("\n🛠️  Step 6: Applying fixes to clean data...")
    if "category" in clean_df.columns:
        clean_df["category"] = clean_df["category"].fillna("Unknown")

    # 10. Mask PII for POPIA compliance
    print("🔒 Step 7: Masking PII (POPIA compliance)...")
    clean_masked = mask_pii(clean_df)

    # 11. Save clean data to Delta Lake (Silver layer)
    print("💾 Step 8: Saving clean data to Delta Lake...")
    output_path = "data/clean/sa_bank_silver"
    try:
        from deltalake import write_deltalake
        write_deltalake(output_path, clean_masked, mode="overwrite")
        print(f"✅ Clean data saved to Delta Lake at: {output_path}")
    except Exception as e:
        print(f"⚠️ Delta Lake write failed ({e}). Saving as CSV fallback...")
        csv_fallback = "data/clean/sa_bank_silver.csv"
        clean_masked.to_csv(csv_fallback, index=False)
        output_path = csv_fallback

    # 12. Run Great Expectations (or basic) DQ checks
    print("\n🔬 Step 9: Running comprehensive DQ checks...")
    dq_results = run_data_quality_checks(clean_df)

    # 13. Compile POPIA-compliant Audit Log
    print("\n📋 Step 10: Creating POPIA-compliant Audit Log...")
    fraud_stats = fraud_summary(df)
    audit = {
        "pipeline_run_time":      datetime.now().isoformat(),
        "pipeline_version":       "2.0.0",
        "raw_records":            len(df),
        "bad_records":            len(bad_records),
        "clean_records":          len(clean_masked),
        "failure_rate_percent":   round(failure_rate, 2),
        "anomaly_method":         anomaly_method,
        "circuit_breaker_state":  breaker.state,
        "circuit_breaker_config": breaker.to_dict(),
        "quarantine_file":        quarantine_path,
        "data_saved_to":          output_path,
        "dq_checks":              dq_results,
        "fraud_scoring":          fraud_stats,
        "issues_detected": [
            "Invalid SA ID numbers",
            "Invalid cellphone formats",
            "Missing customer names",
            "Email format issues",
            "Transaction amount outliers",
        ],
    }

    audit_path = "reports/audit_log.json"
    with open(audit_path, "w") as f:
        json.dump(audit, f, indent=4, default=str)
    print(f"📊 Audit Log saved to {audit_path}")

    # 14. Append to rolling history (for dashboard trend charts)
    log_run(audit)

    # Print summary
    dq_score = dq_results.get("quality_score", round(100 - failure_rate, 1))
    print("\n" + "=" * 55)
    print("📋 PIPELINE SUMMARY")
    print("=" * 55)
    print(f"Raw Records      : {audit['raw_records']:,}")
    print(f"Bad Records      : {audit['bad_records']:,} ({audit['failure_rate_percent']}%)")
    print(f"Clean Records    : {audit['clean_records']:,}")
    print(f"DQ Score         : {dq_score:.1f}%")
    print(f"Fraud Flagged    : {fraud_stats.get('fraud_flagged', 0):,} ({fraud_stats.get('fraud_rate_pct', 0):.1f}%)")
    print(f"Circuit State    : {audit['circuit_breaker_state']}")
    print(f"Status           : ✅ Pipeline completed successfully!")
    print("=" * 55)

    return clean_masked


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 5_000
        clean_data = run_pipeline(n_records=n)

        if clean_data is not None:
            print("\n🎉 SA Bank Data Quality Pipeline finished successfully!")
            print("   ✅ Project ready for GitHub portfolio")
            print("   ▶️  Launch dashboard: streamlit run dashboard.py")
    except KeyboardInterrupt:
        print("\n⚠️  Pipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        print("   💡 Check: 1) dependencies installed, 2) folder permissions, 3) Python 3.9+")
        import traceback
        traceback.print_exc()
        sys.exit(1)