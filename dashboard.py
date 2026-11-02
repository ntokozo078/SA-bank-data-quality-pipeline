"""
SA Bank data quality dashboard
Run with: streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import json
import sys
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="SA Bank DQ Monitor",
    layout="wide",
    initial_sidebar_state="expanded",
)

src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

SILVER_LAYER_PATH = "data/clean/sa_bank_silver"
SILVER_CSV_PATH   = "data/clean/sa_bank_silver.csv"
QUARANTINE_PATH   = "data/quarantine/quarantined_batch.csv"
AUDIT_LOG_PATH    = "reports/audit_log.json"
HISTORY_PATH      = "reports/pipeline_history.jsonl"
CIRCUIT_STATE_PATH = "reports/circuit_state.json"


@st.cache_data(ttl=30)
def load_clean_data() -> pd.DataFrame:
    """Load clean (silver) data - Delta Lake first, CSV fallback."""
    try:
        if Path(SILVER_LAYER_PATH).exists():
            from deltalake import DeltaTable
            return DeltaTable(SILVER_LAYER_PATH).to_pandas()
    except Exception:
        pass
    try:
        if Path(SILVER_CSV_PATH).exists():
            return pd.read_csv(SILVER_CSV_PATH)
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=30)
def load_quarantine_data() -> pd.DataFrame:
    try:
        if Path(QUARANTINE_PATH).exists():
            df = pd.read_csv(QUARANTINE_PATH)
            # Match Silver's types so the two can be combined and displayed together.
            if "transaction_date" in df.columns:
                df["transaction_date"] = pd.to_datetime(df["transaction_date"])
            return df
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=30)
def load_audit_log() -> dict:
    try:
        if Path(AUDIT_LOG_PATH).exists():
            with open(AUDIT_LOG_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


@st.cache_data(ttl=30)
def load_circuit_state() -> dict:
    try:
        if Path(CIRCUIT_STATE_PATH).exists():
            with open(CIRCUIT_STATE_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


@st.cache_data(ttl=30)
def load_history() -> pd.DataFrame:
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from pipeline_logger import get_trend_dataframe
        return get_trend_dataframe(HISTORY_PATH)
    except Exception:
        return pd.DataFrame()


with st.sidebar:
    st.markdown("### Controls")
    if st.button("Refresh Dashboard", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### Pipeline Actions")
    run_pipeline = st.button("Run Main Pipeline", width="stretch")
    if run_pipeline:
        with st.spinner("Running pipeline..."):
            import subprocess, sys as _sys
            result = subprocess.run(
                [_sys.executable, "main.py", "3000"],
                capture_output=True, text=True, cwd=str(Path(__file__).parent)
            )
            if result.returncode == 0:
                st.success("Pipeline completed!")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(f"Pipeline failed:\n```\n{result.stderr[-800:]}\n```")

    st.markdown("---")
    st.markdown("### Info")
    st.markdown("""
- PII is masked with salted SHA-256
- The circuit breaker rejects bad batches
- Fraud scores come from 8 rules
- Silver is a local Delta Lake table
    """)

clean_df      = load_clean_data()
quarantine_df = load_quarantine_data()
audit         = load_audit_log()
history_df    = load_history()

total_clean       = len(clean_df)
total_quarantined = len(quarantine_df)
total_records     = total_clean + total_quarantined
dq_score          = (total_clean / total_records * 100) if total_records > 0 else 100.0
failure_rate      = (total_quarantined / total_records * 100) if total_records > 0 else 0.0

# The breaker's saved state decides this, so the dashboard can't disagree with the pipeline.
circuit       = load_circuit_state()
circuit_limit = circuit.get("max_failure_rate", "-")
circuit_open  = circuit.get("state", "CLOSED") != "CLOSED" or circuit.get("failure_count", 0) > 0

fraud_flagged  = int(clean_df["fraud_flag"].sum())     if "fraud_flag" in clean_df.columns else 0
fraud_flagged += int(quarantine_df["fraud_flag"].sum()) if "fraud_flag" in quarantine_df.columns else 0

st.title("SA Bank Data Quality")
st.caption("Batch results on synthetic South African banking data")

if circuit_open and total_records > 0:
    st.error(
        f"LAST BATCH REJECTED - Circuit {circuit.get('state', '-')}, more than {circuit_limit}% bad records. "
        f"Silver still holds the last accepted batch. Fix upstream data sources and re-run the pipeline."
    )
elif total_records > 0:
    st.success(
        f"Last batch accepted - {dq_score:.1f}% of records passed | "
        f"Circuit CLOSED (halts above {circuit_limit}% bad records)."
    )

st.markdown("---")

kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)

kpi1.metric("Total Records",    f"{total_records:,}")
kpi2.metric("Clean Records",    f"{total_clean:,}")
kpi3.metric("Quarantined",      f"{total_quarantined:,}")
kpi4.metric("Pass Rate",        f"{dq_score:.1f}%")
kpi5.metric("Fraud Flagged",    f"{fraud_flagged:,}",
            delta=f"{fraud_flagged/total_records*100:.1f}% of total" if total_records > 0 else "-",
            delta_color="inverse" if fraud_flagged > 0 else "off")

st.markdown("---")

tab_overview, tab_clean, tab_quarantine, tab_fraud, tab_audit = st.tabs([
    "Overview & Trends",
    "Clean Data",
    "Quarantine",
    "Fraud Analysis",
    "Audit Log",
])


with tab_overview:
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Pass Rate % Over Time")
        if not history_df.empty and "dq_score" in history_df.columns:
            chart_df = history_df[["run_time", "dq_score"]].dropna().set_index("run_time")
            st.line_chart(chart_df, height=220)
        else:
            st.info("No history yet. Run the pipeline to start one.")

    with col_right:
        st.subheader("Failure Rate % Over Time")
        if not history_df.empty and "failure_rate_pct" in history_df.columns:
            chart_df = history_df[["run_time", "failure_rate_pct"]].dropna().set_index("run_time")
            st.line_chart(chart_df, height=220)
        else:
            st.info("No history yet.")

    st.markdown("---")

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.subheader("Category Breakdown")
        src = pd.concat([clean_df, quarantine_df], ignore_index=True)
        if "category" in src.columns and not src.empty:
            cat_counts = src["category"].value_counts().reset_index()
            cat_counts.columns = ["Category", "Count"]
            st.bar_chart(cat_counts.set_index("Category"), height=250)
        else:
            st.info("No category data available.")

    with col_b:
        st.subheader("Failure Reason Breakdown")
        if not quarantine_df.empty:
            reasons = {}
            if "sa_id_valid_flag" in quarantine_df.columns:
                reasons["Invalid SA ID"]    = int((~quarantine_df["sa_id_valid_flag"]).sum())
            if "cellphone_valid" in quarantine_df.columns:
                reasons["Invalid Cellphone"] = int((~quarantine_df["cellphone_valid"]).sum())
            if "email_valid" in quarantine_df.columns:
                reasons["Invalid Email"]    = int((~quarantine_df["email_valid"]).sum())
            if "is_anomaly" in quarantine_df.columns:
                reasons["Amount Anomaly"]   = int(quarantine_df["is_anomaly"].sum())
            if "customer_name" in quarantine_df.columns:
                reasons["Missing Name"]     = int(quarantine_df["customer_name"].isnull().sum())

            if reasons:
                reason_df = pd.DataFrame(list(reasons.items()), columns=["Reason", "Count"])
                reason_df = reason_df[reason_df["Count"] > 0].sort_values("Count", ascending=False)
                st.bar_chart(reason_df.set_index("Reason"), height=250)
            else:
                st.info("No quarantined records.")
        else:
            st.info("No quarantined records.")

    with col_c:
        st.subheader("Amount Distribution")
        amt_src = clean_df if not clean_df.empty else quarantine_df
        if "amount" in amt_src.columns and not amt_src.empty:
            bins   = [0, 500, 2000, 10000, 50000, 200000, float("inf")]
            labels = ["<500", "500-2K", "2K-10K", "10K-50K", "50K-200K", "200K+"]
            amt_src = amt_src.copy()
            amt_src["amount_band"] = pd.cut(amt_src["amount"], bins=bins, labels=labels, right=True)
            band_counts = amt_src["amount_band"].value_counts().sort_index().reset_index()
            band_counts.columns = ["Band", "Count"]
            st.bar_chart(band_counts.set_index("Band"), height=250)
        else:
            st.info("No amount data available.")

    if not history_df.empty:
        st.markdown("---")
        st.subheader("Recent Pipeline Runs")
        display_cols = ["run_time", "raw_records", "clean_records", "bad_records",
                        "failure_rate_pct", "dq_score", "circuit_state"]
        show_cols = [c for c in display_cols if c in history_df.columns]
        st.dataframe(
            history_df[show_cols].tail(10).sort_values("run_time", ascending=False)
                .rename(columns={"dq_score": "pass_rate_pct"}).reset_index(drop=True),
            width="stretch", hide_index=True
        )


with tab_clean:
    st.subheader("Clean & Masked Transactions (Silver Layer)")

    if not clean_df.empty:
        search = st.text_input("Filter by transaction ID, category, or branch", key="clean_search")
        disp = clean_df.copy()
        if search:
            mask = disp.astype(str).apply(lambda col: col.str.contains(search, case=False)).any(axis=1)
            disp = disp[mask]

        st.caption(f"Showing {min(200, len(disp)):,} of {len(disp):,} records")
        st.dataframe(disp.head(200), width="stretch", hide_index=True)

        st.download_button(
            "Download Clean Data (CSV)",
            data=clean_df.to_csv(index=False).encode(),
            file_name=f"sa_bank_clean_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.info("No clean data available yet.\n\nRun the ETL pipeline first:\n```\npython main.py\n```")


with tab_quarantine:
    st.subheader("Quarantined Records")

    if not quarantine_df.empty:
        reason_opts = ["All"]
        if "sa_id_valid_flag" in quarantine_df.columns: reason_opts.append("Invalid SA ID")
        if "cellphone_valid"   in quarantine_df.columns: reason_opts.append("Invalid Cellphone")
        if "email_valid"       in quarantine_df.columns: reason_opts.append("Invalid Email")
        if "is_anomaly"        in quarantine_df.columns: reason_opts.append("Amount Anomaly")

        selected_reason = st.selectbox("Filter by failure reason", reason_opts, key="quarantine_filter")

        qdisp = quarantine_df.copy()
        if selected_reason == "Invalid SA ID" and "sa_id_valid_flag" in qdisp.columns:
            qdisp = qdisp[~qdisp["sa_id_valid_flag"]]
        elif selected_reason == "Invalid Cellphone" and "cellphone_valid" in qdisp.columns:
            qdisp = qdisp[~qdisp["cellphone_valid"]]
        elif selected_reason == "Invalid Email" and "email_valid" in qdisp.columns:
            qdisp = qdisp[~qdisp["email_valid"]]
        elif selected_reason == "Amount Anomaly" and "is_anomaly" in qdisp.columns:
            qdisp = qdisp[qdisp["is_anomaly"]]

        st.caption(f"Showing {min(200, len(qdisp)):,} of {len(qdisp):,} quarantined records")
        st.dataframe(qdisp.head(200), width="stretch", hide_index=True)

        st.download_button(
            "Download Quarantine Data (CSV)",
            data=quarantine_df.to_csv(index=False).encode(),
            file_name=f"sa_bank_quarantine_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.info("No quarantined records.")


with tab_fraud:
    st.subheader("Fraud Risk Analysis")

    all_df = pd.concat([clean_df, quarantine_df], ignore_index=True).drop(columns="sa_id_valid", errors="ignore")

    if "fraud_risk_score" in all_df.columns and not all_df.empty:
        fcol1, fcol2, fcol3 = st.columns(3)
        fcol1.metric("Fraud Flagged",   f"{int(all_df['fraud_flag'].sum()):,}")
        fcol2.metric("Avg Risk Score",  f"{all_df['fraud_risk_score'].mean():.1f}/100")
        fcol3.metric("Max Risk Score",  f"{int(all_df['fraud_risk_score'].max())}/100")

        st.markdown("---")

        col_fd1, col_fd2 = st.columns(2)

        with col_fd1:
            st.subheader("Risk Score Distribution")
            bins   = [0, 10, 20, 30, 40, 60, 80, 100]
            labels = ["0-10", "11-20", "21-30", "31-40", "41-60", "61-80", "81-100"]
            all_df = all_df.copy()
            all_df["risk_band"] = pd.cut(all_df["fraud_risk_score"], bins=bins, labels=labels, right=True)
            band_df = all_df["risk_band"].value_counts().sort_index().reset_index()
            band_df.columns = ["Risk Band", "Count"]
            st.bar_chart(band_df.set_index("Risk Band"), height=250)

        with col_fd2:
            st.subheader("Highest Scoring Flagged Records")
            fraud_cols = ["transaction_id", "amount", "category", "fraud_risk_score", "fraud_flag"]
            show = [c for c in fraud_cols if c in all_df.columns]
            top_fraud = all_df[all_df.get("fraud_flag", pd.Series([False]*len(all_df)))][show] \
                .sort_values("fraud_risk_score", ascending=False).head(50) \
                if "fraud_flag" in all_df.columns else pd.DataFrame()
            if not top_fraud.empty:
                st.dataframe(top_fraud, width="stretch", hide_index=True)
            else:
                st.info("No flagged records.")

        with st.expander("View All Flagged Transactions"):
            if "fraud_flag" in all_df.columns:
                flagged = all_df[all_df["fraud_flag"]].sort_values("fraud_risk_score", ascending=False)
                if not flagged.empty:
                    st.dataframe(flagged.head(500), width="stretch", hide_index=True)
                else:
                    st.info("No flagged records.")
    else:
        st.info(
            "No fraud scoring data yet. Run the pipeline to generate scores:\n"
            "```\npython main.py\n```"
        )


with tab_audit:
    st.subheader("Latest Pipeline Audit Log")

    if audit:
        acol1, acol2, acol3 = st.columns(3)
        acol1.metric("Last Run",      str(audit.get("pipeline_run_time", audit.get("run_time", "-")))[:19])
        acol2.metric("Circuit State", audit.get("circuit_breaker_state", audit.get("circuit_state", "-")))
        acol3.metric("Pipeline Ver",  audit.get("pipeline_version", "1.x"))

        dq = audit.get("dq_checks", {})
        if dq:
            st.markdown("#### Data Quality Check Results")
            dq_disp = {k: v for k, v in dq.items() if k not in ("details", "checked_at")}
            dq_df = pd.DataFrame([dq_disp])
            st.dataframe(dq_df, width="stretch", hide_index=True)

        fs = audit.get("fraud_scoring", {})
        if fs:
            st.markdown("#### Fraud Scoring Results")
            fs_df = pd.DataFrame([fs])
            st.dataframe(fs_df, width="stretch", hide_index=True)

        with st.expander("Full Audit Log (JSON)"):
            st.json(audit)

        st.download_button(
            "Download Audit Log (JSON)",
            data=json.dumps(audit, indent=4, default=str).encode(),
            file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
        )
    else:
        st.info("No audit log found. Run the pipeline to generate one:\n```\npython main.py\n```")


st.markdown("---")
st.caption(
    "All data is synthetic or simulated. Fraud flags are rule-based and untested on real transactions."
)