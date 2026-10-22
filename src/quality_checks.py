# src/quality_checks.py
"""
Data quality checks for SA Bank pipeline.
Uses Great Expectations (v0.18 API) when available; falls back to native pandas checks.
"""
import pandas as pd
from datetime import datetime

try:
    import great_expectations as gx
    GX_AVAILABLE = True
except Exception as e:
    GX_AVAILABLE = False
    print(f"Great Expectations not available ({e}). Using basic checks only.")


def run_data_quality_checks(df: pd.DataFrame) -> dict:
    """
    Run data quality checks. Returns a rich dict suitable for the audit log and dashboard.
    """
    print("\nRunning Data Quality Checks...")

    if GX_AVAILABLE:
        try:
            return _run_gx_checks(df)
        except Exception as e:
            print(f"Great Expectations failed: {e}\n   Falling back to basic checks...")

    return run_basic_quality_checks(df)


def _run_gx_checks(df: pd.DataFrame) -> dict:
    """GX v0.18 in-memory validation using pandas datasource."""
    context = gx.get_context()

    datasource = context.sources.add_or_update_pandas(name="sa_bank_source")
    asset = datasource.add_dataframe_asset(name="transactions")
    batch_request = asset.build_batch_request(dataframe=df)

    suite_name = "sa_bank_suite"
    try:
        suite = context.get_expectation_suite(suite_name)
    except Exception:
        suite = context.add_expectation_suite(suite_name)

    validator = context.get_validator(
        batch_request=batch_request,
        expectation_suite_name=suite_name,
    )

    validator.expect_column_values_to_not_be_null("transaction_id")
    validator.expect_column_values_to_be_unique("transaction_id")
    if "sa_id" in df.columns:
        validator.expect_column_values_to_match_regex("sa_id", r"^\d{13}$", mostly=0.80)
    if "cellphone" in df.columns:
        validator.expect_column_values_to_match_regex("cellphone", r"^0[6-8]\d{8}$", mostly=0.85)
    if "amount" in df.columns:
        validator.expect_column_values_to_be_between("amount", min_value=0.01, max_value=1_000_000, mostly=0.95)
    if "email" in df.columns:
        validator.expect_column_values_to_match_regex(
            "email", r"^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+$", mostly=0.90
        )

    validator.save_expectation_suite(discard_failed_expectations=False)
    results = validator.validate()

    success_pct = results.statistics.get("success_percent", 0) or 0.0
    passed      = int(results.statistics.get("successful_expectations", 0))
    total_exp   = int(results.statistics.get("evaluated_expectations", 1))

    print(f"Great Expectations Score: {success_pct:.1f}% ({passed}/{total_exp} expectations passed)")
    return {
        "source": "great_expectations",
        "quality_score": round(success_pct, 1),
        "status": "PASS" if success_pct >= 85 else "WARN",
        "passed_expectations": passed,
        "total_expectations": total_exp,
        "checked_at": datetime.now().isoformat(),
    }


def run_basic_quality_checks(df: pd.DataFrame) -> dict:
    """Lightweight fallback - no external dependencies beyond pandas."""
    print("\nRunning Basic Quality Checks (Fallback)...")

    total = len(df)
    checks: dict = {"total_records": total}

    def _count(col, condition):
        return int(condition.sum()) if col in df.columns else 0

    checks["missing_sa_id"]        = _count("sa_id", df.get("sa_id", pd.Series()).isnull())
    checks["missing_customer_name"] = _count("customer_name", df.get("customer_name", pd.Series()).isnull())
    checks["missing_email"]         = _count("email", df.get("email", pd.Series()).isnull())

    checks["duplicate_transactions"] = (
        int(df["transaction_id"].duplicated().sum()) if "transaction_id" in df.columns else 0
    )

    if "amount" in df.columns:
        checks["negative_amounts"] = int((df["amount"] < 0).sum())
        checks["zero_amounts"]     = int((df["amount"] == 0).sum())
        checks["extreme_outliers"] = int((df["amount"] > 1_000_000).sum())
    else:
        checks["negative_amounts"] = checks["zero_amounts"] = checks["extreme_outliers"] = 0

    if "sa_id_valid_flag" in df.columns:
        checks["invalid_sa_id"] = int((~df["sa_id_valid_flag"]).sum())
    else:
        checks["invalid_sa_id"] = checks["missing_sa_id"]

    if "cellphone_valid" in df.columns:
        checks["invalid_cellphone"] = int((~df["cellphone_valid"]).sum())
    else:
        checks["invalid_cellphone"] = 0

    if "email_valid" in df.columns:
        checks["invalid_email"] = int((~df["email_valid"]).sum())
    else:
        checks["invalid_email"] = 0

    if "is_anomaly" in df.columns:
        checks["amount_anomalies"] = int(df["is_anomaly"].sum())
    else:
        checks["amount_anomalies"] = checks["extreme_outliers"]

    error_count = (
        checks["invalid_sa_id"]           * 3 +
        checks["invalid_cellphone"]        * 2 +
        checks["invalid_email"]            * 2 +
        checks["negative_amounts"]         * 2 +
        checks["duplicate_transactions"]       +
        checks["amount_anomalies"]
    )
    max_errors = max(total * 0.15, 1)
    score = max(0.0, 100.0 - (error_count / max_errors * 100))

    print(f"   Total records        : {total:,}")
    print(f"   Invalid SA IDs       : {checks['invalid_sa_id']:,}")
    print(f"   Invalid cellphones   : {checks['invalid_cellphone']:,}")
    print(f"   Invalid emails       : {checks['invalid_email']:,}")
    print(f"   Duplicate TX IDs     : {checks['duplicate_transactions']:,}")
    print(f"   Amount anomalies     : {checks['amount_anomalies']:,}")
    print(f"   Quality Score        : {score:.1f}%")

    return {
        "source": "basic_pandas",
        "quality_score": round(score, 1),
        "status": "PASS" if score >= 75 else "WARN",
        "checked_at": datetime.now().isoformat(),
        **checks,
    }