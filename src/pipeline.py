import pandas as pd
from deltalake import write_deltalake
from src.data_generator import generate_messy_sa_data
from src.validators import validate_sa_id, validate_cellphone, validate_email
from src.pii_masker import mask_pii
from src.circuit_breaker import DataCircuitBreaker
from src.quality_checks import run_data_quality_checks  # reuse from previous if you want GX

def run_full_pipeline():
    # 1. Ingest raw data
    df = generate_messy_sa_data(5000)

    # 2. Apply SA-specific validations
    df['sa_id_valid'] = df['sa_id'].astype(str).apply(validate_sa_id)
    df['sa_id_valid_flag'] = df['sa_id_valid'].apply(lambda x: x['valid'])
    df['cellphone_valid'] = df['cellphone'].apply(validate_cellphone)
    df['email_valid'] = df['email'].apply(validate_email)

    # 3. Anomaly detection (amount outliers - common in retail/banking fraud)
    from sklearn.ensemble import IsolationForest
    iso = IsolationForest(contamination=0.05, random_state=42)
    df['amount_anomaly'] = iso.fit_predict(df[['amount']])
    df['is_anomaly'] = df['amount_anomaly'] == -1

    # 4. Identify bad records
    bad_mask = (~df['sa_id_valid_flag'] | 
                ~df['cellphone_valid'] | 
                df['is_anomaly'] | 
                df['customer_name'].isnull() |
                ~df['email_valid'])
    bad_records = df[bad_mask].copy()
    clean_df = df[~bad_mask].copy()

    print(f"\nDetected {len(bad_records)} bad records out of {len(df)}")

    # 5. Quarantine bad records
    bad_records.to_csv("data/quarantine/quarantined_batch.csv", index=False)

    # 6. Circuit Breaker decision
    breaker = DataCircuitBreaker()
    if not breaker.check_batch(len(bad_records), len(df)):
        # Quarantine and stop
        bad_records.to_csv("data/quarantine/bad_batch_circuit_open.csv", index=False)
        print("Pipeline halted for this batch.")
        return None

    # 7. Simple remediation on clean data
    clean_df['category'] = clean_df['category'].fillna('Unknown')

    # 8. Mask PII for POPIA
    clean_masked = mask_pii(clean_df)

    # 9. (Optional) Great Expectations checks on clean data
    # results = run_data_quality_checks(clean_masked)  # add your previous GX function here

    # 10. Save to Delta Lake (versioned Silver layer - auditable)
    output_path = "data/clean/sa_bank_silver"
    write_deltalake(output_path, clean_masked, mode='overwrite')  # or 'append' in real pipelines
    print(f"✅ Clean data saved to Delta Lake at: {output_path} (ready for analytics/ML)")

    # 11. Audit Log (critical for POPIA audits)
    audit = {
        "run_time": datetime.now().isoformat(),
        "raw_records": len(df),
        "bad_records": len(bad_records),
        "clean_records": len(clean_masked),
        "failure_rate_pct": round(len(bad_records)/len(df)*100, 2),
        "circuit_state": breaker.state,
        "issues_found": ["Invalid SA IDs", "Invalid cellphones", "Outliers", "Missing PII", "Duplicates"]
    }
    print("\n📋 POPIA Audit Log:")
    for k, v in audit.items():
        print(f"   {k}: {v}")

    return clean_masked

if __name__ == "__main__":
    run_full_pipeline()