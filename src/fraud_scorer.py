# src/fraud_scorer.py
"""
Rule-based fraud risk scoring for SA Bank DQ Pipeline.

Assigns each transaction a fraud_risk_score (0-100) and a boolean fraud_flag.
Designed to be fast, explainable, and easy to tune for SA retail banking patterns.

Rules (cumulative scoring)
--------------------------
R1  High amount (> R50,000)               -> +35
R2  Extreme amount (> R200,000)           -> +20 additional
R3  Duplicate transaction ID              -> +40
R4  Night-time transaction (00:00-05:00)  -> +15
R5  Cellphone invalid (but ID valid)      -> +10  (mule account signal)
R6  Missing customer name                 -> +15
R7  Known high-risk category              -> +10  (Cash_Out / CashOut / Withdrawal)
R8  Round-number amount (mod 1000 == 0)   -> +8   (structuring signal)
"""
import pandas as pd
import numpy as np
from typing import Optional


HIGH_AMOUNT_THRESHOLD    = 50_000.0
EXTREME_AMOUNT_THRESHOLD = 200_000.0
NIGHT_HOUR_START         = 0
NIGHT_HOUR_END           = 5
HIGH_RISK_CATEGORIES     = {"cash_out", "cashout", "withdrawal"}
FRAUD_FLAG_THRESHOLD     = 40   # score >= this -> flagged


def score_fraud_risk(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add fraud_risk_score (int 0-100) and fraud_flag (bool) columns to df.

    Parameters
    ----------
    df : DataFrame with at minimum an 'amount' column.
         Optional columns: transaction_date, transaction_id, cellphone_valid,
         sa_id_valid_flag, customer_name, category.

    Returns
    -------
    df (copy) with two new columns appended.
    """
    df = df.copy()
    score = pd.Series(np.zeros(len(df), dtype=int), index=df.index)

    # R1 / R2 - high amounts
    if "amount" in df.columns:
        score += np.where(df["amount"] > HIGH_AMOUNT_THRESHOLD, 35, 0)
        score += np.where(df["amount"] > EXTREME_AMOUNT_THRESHOLD, 20, 0)

        # R8 - round-number structuring signal
        score += np.where(
            (df["amount"] % 1000 == 0) & (df["amount"] >= 5_000),
            8, 0
        )

    # R3 - duplicate transaction IDs
    if "transaction_id" in df.columns:
        dupes = df["transaction_id"].duplicated(keep=False)
        score += np.where(dupes, 40, 0)

    # R4 - night-time transactions
    if "transaction_date" in df.columns:
        try:
            hour = pd.to_datetime(df["transaction_date"]).dt.hour
            score += np.where(
                (hour >= NIGHT_HOUR_START) & (hour < NIGHT_HOUR_END),
                15, 0
            )
        except Exception:
            pass

    # R5 - invalid cellphone but valid ID (possible mule account)
    if "cellphone_valid" in df.columns and "sa_id_valid_flag" in df.columns:
        score += np.where(
            (~df["cellphone_valid"]) & df["sa_id_valid_flag"],
            10, 0
        )

    # R6 - missing customer name
    if "customer_name" in df.columns:
        score += np.where(df["customer_name"].isnull(), 15, 0)

    # R7 - high-risk category
    if "category" in df.columns:
        cat_lower = df["category"].astype(str).str.lower().str.strip()
        score += np.where(cat_lower.isin(HIGH_RISK_CATEGORIES), 10, 0)

    df["fraud_risk_score"] = score.clip(upper=100).astype(int)
    df["fraud_flag"]       = df["fraud_risk_score"] >= FRAUD_FLAG_THRESHOLD

    flagged = df["fraud_flag"].sum()
    pct     = flagged / len(df) * 100 if len(df) > 0 else 0
    print(
        f"Fraud scoring complete | {flagged:,} flagged ({pct:.1f}%) | "
        f"Avg risk score: {df['fraud_risk_score'].mean():.1f}"
    )

    return df


def fraud_summary(df: pd.DataFrame) -> dict:
    """Return a summary dict for audit logging."""
    if "fraud_risk_score" not in df.columns:
        return {}
    return {
        "total_scored":      len(df),
        "fraud_flagged":     int(df["fraud_flag"].sum()),
        "fraud_rate_pct":    round(df["fraud_flag"].mean() * 100, 2),
        "avg_risk_score":    round(df["fraud_risk_score"].mean(), 1),
        "max_risk_score":    int(df["fraud_risk_score"].max()),
        "high_amount_flags": int((df["amount"] > HIGH_AMOUNT_THRESHOLD).sum()) if "amount" in df.columns else 0,
        "night_txn_flags":   int(
            (pd.to_datetime(df["transaction_date"]).dt.hour.between(0, 4)).sum()
        ) if "transaction_date" in df.columns else 0,
    }
