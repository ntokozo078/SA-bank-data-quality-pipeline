# src/pii_masker.py
"""
POPIA-compliant PII masking for South African banking data.

Two masking modes
-----------------
hash  (default) - one-way SHA-256 hash, truncated to 12 chars + "..."
token           - UUID-based pseudonymisation stored in a lookup table (reversible by authorised users)
"""
import hashlib
import uuid
import json
import os
import pandas as pd
from pathlib import Path
from datetime import datetime


TOKEN_STORE_PATH = "reports/pii_token_store.json"


def _load_token_store() -> dict:
    if Path(TOKEN_STORE_PATH).exists():
        try:
            with open(TOKEN_STORE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_token_store(store: dict):
    Path(TOKEN_STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)


def mask_pii(
    df: pd.DataFrame,
    salt: str | None = None,
    mask_mode: str = "hash",          # "hash" or "token"
    pii_cols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Mask PII columns for POPIA compliance.

    Parameters
    ----------
    df        : DataFrame to mask (a copy is always returned)
    salt      : Optional salt for hash mode. Falls back to PII_SALT env var.
    mask_mode : 'hash'  -> SHA-256 truncated (irreversible, fast)
                'token' -> UUID pseudonymisation with lookup table (reversible)
    pii_cols  : Columns to mask. Defaults to standard PII columns.

    Returns
    -------
    Masked copy of df. Original is never modified.
    """
    df = df.copy()

    if pii_cols is None:
        pii_cols = ["customer_name", "sa_id", "cellphone", "email"]

    present_cols = [c for c in pii_cols if c in df.columns]

    if mask_mode == "token":
        store = _load_token_store()
        for col in present_cols:
            def _tokenise(x, _store=store, _col=col):
                if pd.isna(x) or x is None:
                    return None
                key = f"{_col}::{x}"
                if key not in _store:
                    _store[key] = f"TOK-{uuid.uuid4().hex[:10].upper()}"
                return _store[key]
            df[col] = df[col].apply(_tokenise)
        _save_token_store(store)
        mode_label = "token (UUID pseudonymisation)"
    else:
        if salt is None:
            salt = os.getenv("PII_SALT", "sa_bank_default_salt_2026")
        for col in present_cols:
            df[col] = df[col].apply(
                lambda x: hashlib.sha256((str(x) + salt).encode()).hexdigest()[:12] + "..."
                if pd.notnull(x) else None
            )
        mode_label = "SHA-256 hash"

    print(
        f"PII masked [{mode_label}] | "
        f"Columns: {present_cols} | "
        f"Rows: {len(df):,} | "
        f"At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return df