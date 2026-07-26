# etl_paysim.py
"""
ETL Pipeline for PaySim Financial Dataset.
Transforms raw PaySim data with SA-specific quality checks, fraud scoring,
anomaly detection, PII masking, and Delta Lake output.

Usage
-----
    python etl_paysim.py --input data/raw/PS_20174392719_1491204439457_log.csv
    python etl_paysim.py --input data/raw/PS_...csv --sample 10000
"""
import pandas as pd
import numpy as np
from datetime import datetime
import json
from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from validators import validate_sa_id, validate_cellphone, validate_email
from pii_masker import mask_pii
from circuit_breaker import DataCircuitBreaker
from fraud_scorer import score_fraud_risk, fraud_summary
from pipeline_logger import log_run

SA_CITIES = [
    "Johannesburg", "Cape Town", "Durban", "Pretoria", "Port Elizabeth",
    "Bloemfontein", "East London", "Polokwane", "Nelspruit", "Kimberley",
]


# ---------------------------------------------------------------------------
# Step 1: Load
# ---------------------------------------------------------------------------

def load_paysim_data(csv_path: str) -> pd.DataFrame:
    """Load PaySim dataset from CSV."""
    print(f"📥 Loading PaySim data from {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"✅ Loaded {len(df):,} records | Columns: {list(df.columns)}")
    return df


# ---------------------------------------------------------------------------
# Step 2: Enrich with SA fields
# ---------------------------------------------------------------------------

def enrich_with_sa_fields(df: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """Add realistic SA-specific fields to PaySim data."""
    print("\n🔧 Enriching data with SA-specific fields...")
    rng = np.random.default_rng(seed)
    n = len(df)

    # --- SA IDs (80% valid Luhn format, 20% broken) ---
    from data_generator import generate_valid_sa_id, generate_invalid_sa_id
    sa_ids = [
        generate_valid_sa_id(rng) if rng.random() < 0.80 else generate_invalid_sa_id(rng)
        for _ in range(n)
    ]

    # --- Cellphones (85% valid mobile, 15% landline) ---
    def gen_phone():
        if rng.random() < 0.85:
            prefix = rng.choice([60, 72, 73, 76, 81, 82, 83])
            return f"0{prefix}{rng.integers(1_000_000, 9_999_999):07d}"
        return f"011{rng.integers(1_000_000, 9_999_999):07d}"


    # --- Emails (90% valid, 10% broken) ---
    domains = ["gmail.com", "yahoo.com", "outlook.com", "webmail.co.za"]

    def gen_email():
        if rng.random() < 0.90:
            return f"user{rng.integers(1000, 9999)}@{rng.choice(domains)}"
        return "invalid.email.format"

    df = df.copy()
    df["sa_id"]           = sa_ids
    df["cellphone"]       = [gen_phone() for _ in range(n)]
    df["email"]           = [gen_email() for _ in range(n)]
    df["customer_name"]   = [f"Cust_{i}" if rng.random() > 0.05 else None for i in range(n)]
    df["store_branch"]    = [rng.choice(SA_CITIES) if rng.random() > 0.1 else None for _ in range(n)]
    df["category"]        = df["type"].map({
        "PAYMENT": "Payment", "TRANSFER": "Transfer",
        "DEBIT": "Withdrawal", "CASH_OUT": "CashOut", "CASH_IN": "Deposit",
    }).fillna("Unknown")
    df["transaction_id"]  = [f"TXN_{i:08d}" for i in range(n)]
    df["transaction_date"] = pd.date_range(start="2026-04-01", periods=n, freq="min")

    print("   ✅ Added SA fields with realistic quality noise")
    return df


# ---------------------------------------------------------------------------
# Step 3: Light cleaning
# ---------------------------------------------------------------------------

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Cap extreme outliers and fill critical missing values."""
    cap = df["amount"].quantile(0.99)
    df["amount"] = df["amount"].clip(upper=cap)
    df["customer_name"] = df["customer_name"].fillna("Unknown_Customer")
    return df


# ---------------------------------------------------------------------------
# Step 4: Validate
# ---------------------------------------------------------------------------

def validate_paysim_data(df: pd.DataFrame) -> pd.DataFrame:
    """Run SA-specific validations and IQR-based anomaly detection."""
    print("\n🔍 Running SA-specific validations...")

    df["sa_id_valid"]       = df["sa_id"].astype(str).apply(validate_sa_id)
    df["sa_id_valid_flag"]  = df["sa_id_valid"].apply(lambda x: x["valid"])
    df["cellphone_valid"]   = df["cellphone"].apply(validate_cellphone)
    df["email_valid"]       = df["email"].apply(validate_email)
    df["pii_complete"]      = ~(df["customer_name"].isnull() | df["sa_id"].isnull())

    # IQR anomaly detection (fast & stable for large datasets)
    q1, q3 = df["amount"].quantile([0.25, 0.75])
    iqr     = q3 - q1
    df["is_anomaly"] = (df["amount"] < q1 - 3 * iqr) | (df["amount"] > q3 + 3 * iqr)

    return df


# ---------------------------------------------------------------------------
# Main ETL
# ---------------------------------------------------------------------------

def run_paysim_etl(input_path: str, sample_size: int | None = None) -> pd.DataFrame | None:
    """Main PaySim ETL pipeline."""
    print("🚀 Starting PaySim ETL Pipeline...\n")

    for d in ["data/raw", "data/clean", "data/quarantine", "reports", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    df = load_paysim_data(input_path)

    if sample_size and sample_size < len(df):
        print(f"📊 Sampling {sample_size:,} records for testing...")
        df = df.sample(n=sample_size, random_state=42).reset_index(drop=True)

    df = enrich_with_sa_fields(df)
    df = clean_data(df)
    df = validate_paysim_data(df)

    # Fraud scoring
    print("\n🔍 Scoring fraud risk...")
    df = score_fraud_risk(df)

    # Split clean / bad
    bad_mask    = (~df["sa_id_valid_flag"]) | (~df["cellphone_valid"]) | (~df["email_valid"]) | df["is_anomaly"]
    bad_records = df[bad_mask].copy()
    clean_df    = df[~bad_mask].copy()
    failure_rate = len(bad_records) / len(df) * 100 if len(df) > 0 else 0

    print(f"\n📉 Quality Check Results:")
    print(f"   Total records : {len(df):,}")
    print(f"   Bad records   : {len(bad_records):,} ({failure_rate:.1f}%)")
    print(f"   Clean records : {len(clean_df):,}")

    # Quarantine
    bad_records.to_csv("data/quarantine/quarantined_batch.csv", index=False)

    # Circuit Breaker
    print("\n⚡ Circuit Breaker Evaluation...")
    breaker = DataCircuitBreaker(failure_threshold=5, max_failure_rate=30.0)

    if not breaker.check_batch(len(bad_records), len(df)):
        print("⛔ Pipeline halted by circuit breaker.")
        return None

    # Mask & Save
    print("\n🔒 Masking PII & Saving to Delta Lake...")
    clean_masked = mask_pii(clean_df)

    output_path = "data/clean/sa_bank_silver"
    try:
        from deltalake import write_deltalake
        write_deltalake(output_path, clean_masked, mode="overwrite")
    except Exception as e:
        print(f"⚠️ Delta Lake write failed ({e}). Using CSV fallback...")
        csv_path = "data/clean/sa_bank_silver.csv"
        clean_masked.to_csv(csv_path, index=False)
        output_path = csv_path

    # Audit Log
    fraud_stats = fraud_summary(df)
    audit = {
        "run_time":            datetime.now().isoformat(),
        "pipeline_version":    "2.0.0",
        "source":              input_path,
        "raw_records":         len(df),
        "bad_records":         len(bad_records),
        "clean_records":       len(clean_masked),
        "failure_rate_pct":    round(failure_rate, 2),
        "circuit_state":       breaker.state,
        "fraud_scoring":       fraud_stats,
        "data_saved_to":       output_path,
    }
    # Normalise keys for pipeline_logger compatibility
    audit["pipeline_run_time"]     = audit["run_time"]
    audit["failure_rate_percent"]  = audit["failure_rate_pct"]

    with open("reports/audit_log.json", "w") as f:
        json.dump(audit, f, indent=4)

    log_run(audit)

    print(f"\n✅ ETL complete | Clean data → {output_path}")
    return clean_masked


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PaySim ETL pipeline for SA Bank DQ")
    parser.add_argument("--input",  default="data/raw/PS_20174392719_1491204439457_log.csv",
                        help="Path to the PaySim CSV file")
    parser.add_argument("--sample", type=int, default=10_000,
                        help="Number of rows to sample (default 10,000; 0 = use all)")
    args = parser.parse_args()

    sample = args.sample if args.sample > 0 else None
    try:
        run_paysim_etl(args.input, sample)
        print("\n🎉 ETL finished successfully!")
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)