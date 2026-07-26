# 🏦 SA Bank Data Quality & POPIA Pipeline

[![Live Dashboard on Render](https://img.shields.io/badge/Live%20Dashboard-View%20on%20Render-46E3B7?style=for-the-badge&logo=render)](https://sa-bank-data-quality-pipeline-1.onrender.com/)

> A production-grade South African banking data quality pipeline featuring POPIA-compliant PII masking, ML-based anomaly detection, rule-based fraud scoring, circuit breaker fault tolerance, Delta Lake storage, and a real-time monitoring dashboard.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔒 **POPIA Compliance** | SHA-256 PII hashing *or* UUID tokenisation for customer_name, SA ID, cellphone, email |
| 🇿🇦 **SA Validators** | Luhn-checksum SA ID, mobile cellphone format (06x/07x/08x), email, amount, bank account |
| 🤖 **Anomaly Detection** | IsolationForest (sklearn) with IQR statistical fallback |
| 🔍 **Fraud Scoring** | 8 explainable rules → `fraud_risk_score` (0–100) + `fraud_flag` per transaction |
| ⚡ **Circuit Breaker** | 3-state machine (CLOSED → OPEN → HALF_OPEN) with persistent JSON state |
| 🏠 **Delta Lake** | Parquet-based Silver layer storage with CSV fallback |
| 📊 **Dashboard** | Streamlit dashboard with dark theme, trend charts, fraud analysis, pipeline runner |
| 📅 **Scheduler** | APScheduler with cron / interval triggers, retry logic, webhook alerts |
| 📝 **Audit Logging** | Per-run JSONL history for trend visualisation + JSON audit log |

---

## 🗂️ Project Architecture

```
Raw Data (CSV / PaySim)
       │
       ▼
┌─────────────────────────────────────────────┐
│              BRONZE LAYER (Raw)             │
│  data/raw/messy_sa_transactions.csv         │
└──────────────────────┬──────────────────────┘
                       │
          ┌────────────▼─────────────┐
          │   SA-Specific Validators │  validators.py
          │   • SA ID (Luhn)         │
          │   • Cellphone (0[6-8]x)  │
          │   • Email, Amount        │
          └────────────┬─────────────┘
                       │
          ┌────────────▼─────────────┐
          │   Anomaly Detection      │  main.py / etl_paysim.py
          │   IsolationForest / IQR  │
          └────────────┬─────────────┘
                       │
          ┌────────────▼─────────────┐
          │   Fraud Risk Scorer      │  src/fraud_scorer.py
          │   8 rules → score 0–100  │
          └────────────┬─────────────┘
                       │
               ┌───────┴───────┐
               │               │
        ✅ CLEAN          ⚠️ BAD RECORDS
               │               │
               ▼               ▼
        ┌──────────┐    ┌─────────────────┐
        │ PII Mask │    │   Quarantine    │
        │ POPIA 🔒 │    │  data/quarantine│
        └────┬─────┘    └─────────────────┘
             │
             ▼
   ┌──────────────────────┐
   │  SILVER LAYER        │  Delta Lake (Parquet)
   │  data/clean/         │  + CSV fallback
   └──────────┬───────────┘
              │
   ┌──────────▼───────────┐
   │  Audit Log + History │  reports/audit_log.json
   │  JSONL Trend Log     │  reports/pipeline_history.jsonl
   └──────────┬───────────┘
              │
   ┌──────────▼───────────┐
   │  Streamlit Dashboard │  dashboard.py
   │  DQ Score · Fraud    │  localhost:8501
   │  Trends · Audit Log  │
   └──────────────────────┘
```

---

## ⚡ Quickstart

### 1. Clone & install

```bash
git clone https://github.com/ntokozo078/SA-bank-data-quality-pipeline.git
cd SA-bank-data-quality-pipeline
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Run the synthetic data pipeline

```bash
# Generate 5,000 messy SA records, validate, score fraud, mask PII, save to Delta Lake
python main.py

# Custom record count
python main.py 10000
```

### 3. Launch the dashboard

```bash
streamlit run dashboard.py
# Open http://localhost:8501
```

### 4. Run PaySim ETL (optional — requires PaySim dataset)

Download the PaySim dataset from Kaggle and place in `data/raw/`:

```bash
python etl_paysim.py --input data/raw/PS_20174392719_1491204439457_log.csv --sample 10000
```

### 5. Start the automation scheduler

```bash
# Hourly scheduled runs
python automation_runner.py

# One-off run (no scheduler)
python automation_runner.py --once
```

---

## 📁 Directory Structure

```
sa-bank-data-quality-pipeline/
│
├── main.py                    # Full pipeline entry point
├── etl_paysim.py              # PaySim-specific ETL
├── dashboard.py               # Streamlit monitoring dashboard
├── automation_runner.py       # APScheduler + retry orchestrator
├── requirements.txt
├── README.md
│
├── src/
│   ├── data_generator.py      # Synthetic SA data with Luhn SA IDs
│   ├── validators.py          # SA ID / cellphone / email / amount validators
│   ├── pii_masker.py          # POPIA-compliant hash or token masking
│   ├── quality_checks.py      # Great Expectations or pandas fallback
│   ├── circuit_breaker.py     # 3-state circuit breaker (CLOSED/OPEN/HALF_OPEN)
│   ├── fraud_scorer.py        # Rule-based fraud risk scoring
│   └── pipeline_logger.py     # Rolling JSONL audit history
│
├── data/
│   ├── raw/                   # Input CSV files
│   ├── clean/sa_bank_silver/  # Delta Lake Silver layer
│   └── quarantine/            # Bad records
│
├── reports/
│   ├── audit_log.json         # Latest run audit
│   ├── pipeline_history.jsonl # Rolling run history (dashboard trends)
│   ├── circuit_state.json     # Persistent circuit breaker state
│   └── pii_token_store.json   # UUID token lookup (token mode only)
│
└── logs/
    └── pipeline_automation.log
```

---

## 🔒 POPIA Compliance

The pipeline implements two PII protection modes:

| Mode | Method | Reversible | Use Case |
|------|--------|-----------|----------|
| `hash` (default) | SHA-256 + salt, truncated 12 chars | ❌ No | Analytics, reporting |
| `token` | UUID pseudonymisation + lookup table | ✅ Yes (authorised access only) | Data sharing between teams |

Masked fields: `customer_name`, `sa_id`, `cellphone`, `email`

Set `PII_SALT` as an environment variable to override the default salt:
```bash
set PII_SALT=my_secret_salt_2026    # Windows
export PII_SALT=my_secret_salt_2026 # macOS/Linux
```

---

## ⚡ Circuit Breaker States

```
         failure_rate > threshold         recovery_seconds elapsed
CLOSED ─────────────────────────► OPEN ──────────────────────────► HALF_OPEN
  ▲                                                                     │
  │                        trial batch passes                           │
  └─────────────────────────────────────────────────────────────────────┘
```

State is persisted in `reports/circuit_state.json` so restarts preserve history.

---

## 🔍 Fraud Scoring Rules

| Rule | Condition | Score |
|------|-----------|-------|
| R1 | Amount > R50,000 | +35 |
| R2 | Amount > R200,000 | +20 (additional) |
| R3 | Duplicate transaction ID | +40 |
| R4 | Transaction between 00:00–05:00 | +15 |
| R5 | Invalid cellphone + valid SA ID | +10 |
| R6 | Missing customer name | +15 |
| R7 | Category = CashOut / Withdrawal | +10 |
| R8 | Round amount ≥ R5,000 (mod 1000 = 0) | +8 |

Records with `fraud_risk_score ≥ 40` are flagged (`fraud_flag = True`).

---

## 🧪 Running Tests

```bash
# Validate SA ID logic
python -c "
from src.validators import validate_sa_id
print(validate_sa_id('9202204720082'))  # valid
print(validate_sa_id('1234567890'))     # invalid (too short)
"

# Test fraud scorer
python -c "
import pandas as pd
import sys; sys.path.insert(0,'src')
from fraud_scorer import score_fraud_risk
df = pd.DataFrame({'amount': [500, 250000], 'transaction_id': ['T1','T2']})
print(score_fraud_risk(df)[['amount','fraud_risk_score','fraud_flag']])
"

# Run full pipeline
python main.py 1000
```

---

## 🚀 Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.9+ |
| Data processing | pandas, numpy |
| Storage | Delta Lake (deltalake), CSV |
| Validation | python-stdnum, regex |
| Anomaly detection | scikit-learn IsolationForest / IQR fallback |
| Data quality | Great Expectations 0.18 / pandas fallback |
| Scheduler | APScheduler 3.x |
| Dashboard | Streamlit |
| Synthetic data | Faker (en_ZA locale) |

---

## 📄 Licence

MIT — Free to use for portfolio projects, learning, and commercial use.

---

*Built for South African retail banking data engineering portfolio · 2026*