# src/data_generator.py
"""
Generates realistic, intentionally messy South African banking transaction data.
Uses a proper Luhn-based SA ID builder so validation logic is actually testable.
"""
import pandas as pd
import numpy as np
from faker import Faker
from datetime import datetime
import os

os.makedirs("data/raw", exist_ok=True)

try:
    fake = Faker("en_US")   # en_ZA not available in all Faker versions; en_US is closest for names
except Exception:
    fake = Faker()


SA_CITIES = [
    "Johannesburg", "Cape Town", "Durban", "Pretoria", "Port Elizabeth",
    "Bloemfontein", "East London", "Polokwane", "Nelspruit", "Kimberley",
    "George", "Rustenburg", "Pietermaritzburg", "Umtata", "Welkom",
]


def _luhn_check_digit(partial: str) -> str:
    """Compute the Luhn check digit for the first 12 digits of an SA ID."""
    total = 0
    for i, d in enumerate(reversed(partial)):
        n = int(d)
        if i % 2 == 0:      # even positions from right -> double
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - (total % 10)) % 10)


def generate_valid_sa_id(rng: np.random.Generator) -> str:
    """Generate a structurally valid 13-digit SA ID with correct Luhn checksum."""
    year = rng.integers(40, 99)            # born 1940-1999
    month = rng.integers(1, 13)
    day = rng.integers(1, 29)
    sequence = rng.integers(1000, 9999)    # gender/sequence block
    citizenship = rng.integers(0, 2)       # 0=SA citizen, 1=permanent resident
    partial = f"{year:02d}{month:02d}{day:02d}{sequence:04d}{citizenship}8"  # digit 11 always 8
    check = _luhn_check_digit(partial)
    return partial + check


def generate_invalid_sa_id(rng: np.random.Generator) -> str:
    """Return a clearly invalid SA ID (wrong length or non-numeric)."""
    choice = rng.integers(0, 3)
    if choice == 0:
        return str(rng.integers(100_000_000, 999_999_999))              # too short (9 digits)
    elif choice == 1:
        return "ABCDE" + str(rng.integers(10_000_000, 99_999_999))      # non-numeric
    else:
        return str(rng.integers(10_000_000_000_000, 99_999_999_999_999)) # too long (14 digits)


def generate_messy_sa_data(n: int = 5_000, seed: int = 42) -> pd.DataFrame:
    """
    Generate realistic messy South African banking transaction data.

    Parameters
    ----------
    n    : number of records to generate
    seed : random seed for reproducibility

    Returns
    -------
    pd.DataFrame saved to data/raw/messy_sa_transactions.csv
    """
    rng = np.random.default_rng(seed)
    Faker.seed(seed)

    # SA IDs: 80% valid, 20% intentionally broken
    sa_ids = [
        generate_valid_sa_id(rng) if rng.random() < 0.80 else generate_invalid_sa_id(rng)
        for _ in range(n)
    ]

    # Cellphones: 85% valid mobile, 15% landline/broken
    def gen_phone():
        if rng.random() < 0.85:
            prefix = rng.choice([60, 61, 72, 73, 74, 76, 78, 81, 82, 83, 84])
            return f"0{prefix}{rng.integers(1_000_000, 9_999_999):07d}"
        else:
            area = rng.choice(["011", "021", "031", "012"])
            return f"{area}{rng.integers(1_000_000, 9_999_999):07d}"

    # Emails: 90% valid, 10% broken
    domains = ["gmail.com", "yahoo.com", "outlook.com", "webmail.co.za", "vodamail.co.za"]

    def gen_email():
        if rng.random() < 0.90:
            return f"user{rng.integers(1000, 9999)}@{rng.choice(domains)}"
        return rng.choice(["invalid.email.format", "no-at-sign", "missing@", "@nodomain"])

    # Transaction amounts: log-normal, realistic SA retail range
    amounts = np.abs(rng.lognormal(mean=6.5, sigma=1.3, size=n)).round(2)

    # Customer names: 5% missing
    names = [fake.name() if rng.random() > 0.05 else None for _ in range(n)]

    # Store branches: 10% missing
    branches = [
        rng.choice(SA_CITIES) if rng.random() > 0.1 else None
        for _ in range(n)
    ]

    data = {
        "transaction_id":   [f"TXN_{i:08d}" for i in range(n)],
        "customer_name":    names,
        "sa_id":            sa_ids,
        "cellphone":        [gen_phone() for _ in range(n)],
        "email":            [gen_email() for _ in range(n)],
        "amount":           amounts,
        "transaction_date": pd.date_range(start="2026-01-01", periods=n, freq="h"),
        "category":         rng.choice(
            ["Deposit", "Withdrawal", "Payment", "Transfer", None],
            n, p=[0.35, 0.30, 0.20, 0.10, 0.05]
        ),
        "store_branch":     branches,
    }
    df = pd.DataFrame(data)

    # Inject extra quality issues (~5-10% on top of the base noise)
    step = max(1, n // 200)
    df.loc[::step * 5,  "amount"]         = df.loc[::step * 5,  "amount"] * 25     # extreme outliers
    df.loc[::step * 7,  "customer_name"]  = None                                    # missing PII
    df.loc[::step * 10, "category"]       = np.nan                                  # missing category
    df.loc[::step * 15, "transaction_id"] = df["transaction_id"].iloc[0]            # duplicate IDs

    output_path = "data/raw/messy_sa_transactions.csv"
    df.to_csv(output_path, index=False)
    print(f"Generated {n:,} records with SA-specific quality noise -> {output_path}")
    return df