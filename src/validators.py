# src/validators.py
"""
SA-specific field validators for POPIA-compliant banking data quality checks.
Covers SA ID numbers (with Luhn checksum), cellphones, emails,
transaction amounts, and basic bank account number formats.
"""
import re
import pandas as pd

try:
    from stdnum.za import idnr
    STDNUM_AVAILABLE = True
except ImportError:
    STDNUM_AVAILABLE = False
    print("python-stdnum not installed. SA ID validation will use basic format check only.")


def _luhn_checksum(number: str) -> int:
    """Compute Luhn check digit for a numeric string."""
    total = 0
    reverse_digits = number[::-1]
    for i, d in enumerate(reverse_digits):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10


def validate_sa_id(sid):
    """
    Validate a South African ID number.
    - Must be exactly 13 numeric digits
    - Encodes YYMMDD (date of birth), citizenship (0/1), and a Luhn check digit
    Returns dict: {"valid": bool, "reason": str}
    """
    if pd.isna(sid) or sid is None:
        return {"valid": False, "reason": "Null value"}

    sid = str(sid).strip()

    if len(sid) != 13:
        return {"valid": False, "reason": f"Wrong length ({len(sid)}, expected 13)"}

    if not sid.isdigit():
        return {"valid": False, "reason": "Contains non-numeric characters"}

    # Basic date sanity: YYMMDD
    try:
        month = int(sid[2:4])
        day = int(sid[4:6])
        if not (1 <= month <= 12):
            return {"valid": False, "reason": f"Invalid month ({month})"}
        if not (1 <= day <= 31):
            return {"valid": False, "reason": f"Invalid day ({day})"}
    except ValueError:
        return {"valid": False, "reason": "Date parse error"}

    # Citizenship digit must be 0 or 1
    citizenship = int(sid[10])
    if citizenship not in (0, 1):
        return {"valid": False, "reason": f"Invalid citizenship digit ({citizenship})"}

    if STDNUM_AVAILABLE:
        try:
            if idnr.is_valid(sid):
                return {"valid": True, "reason": "OK (Luhn verified)"}
            return {"valid": False, "reason": "Luhn checksum failed"}
        except Exception:
            return {"valid": False, "reason": "Parse error"}
    else:
        if _luhn_checksum(sid) == 0:
            return {"valid": True, "reason": "OK (manual Luhn)"}
        return {"valid": False, "reason": "Luhn checksum failed (manual)"}


def validate_cellphone(phone) -> bool:
    """
    Validate SA cellphone: must start with 06x, 07x, or 08x (10 digits total).
    Landlines (011..., 021..., etc.) are rejected.
    """
    if pd.isna(phone) or phone is None:
        return False
    return bool(re.match(r'^0[6-8]\d{8}$', str(phone).strip()))


def validate_email(email) -> bool:
    """Basic RFC-5321 inspired email format validation."""
    if pd.isna(email) or email is None:
        return False
    return bool(re.match(
        r'^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]+$',
        str(email).strip()
    ))


def validate_amount(amount, min_val: float = 0.01, max_val: float = 1_000_000.0) -> bool:
    """
    Validate a transaction amount:
    - Must be numeric and finite
    - Must be within [min_val, max_val] (R0.01 - R1,000,000 for retail banking)
    """
    try:
        val = float(amount)
        return min_val <= val <= max_val
    except (TypeError, ValueError):
        return False


def validate_bank_account(account) -> bool:
    """
    Basic SA bank account number validation.
    South African accounts are typically 9-11 digits.
    Does NOT perform checksum verification (requires bank-specific algorithms).
    """
    if pd.isna(account) or account is None:
        return False
    cleaned = re.sub(r'\s+', '', str(account))
    return bool(re.match(r'^\d{9,11}$', cleaned))