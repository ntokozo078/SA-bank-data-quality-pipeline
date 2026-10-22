# automation_runner.py
"""
Automated Orchestrator for SA Bank Data Quality Pipeline.
Runs on schedule, handles retries, and logs execution to file.

Usage:
    python automation_runner.py           # Start scheduler (hourly by default)
    python automation_runner.py --once    # Run once and exit (no scheduler)
"""
import os
import sys
import time
import logging
import json
import argparse
from pathlib import Path
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "src"))

from etl_paysim import run_paysim_etl

CONFIG = {
    "SCHEDULE_TYPE":        "cron",         # "cron" or "interval"
    "CRON_EXPRESSION":      "0 * * * *",    # Every hour (min hour dom mon dow)
    "INTERVAL_SECONDS":     3600,           # Fallback: every 1 hour
    "MAX_RETRIES":          3,
    "RETRY_DELAY_SECONDS":  30,
    "SAMPLE_SIZE":          10_000,         # Rows per scheduled run (0 = full dataset)
    "LOG_FILE":             "logs/pipeline_automation.log",
    "WATCH_DIR":            "data/raw",
    "ALERT_ON_FAILURE":     True,
    "ALERT_WEBHOOK":        None,           # e.g., Discord/Slack webhook URL
}

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["LOG_FILE"], encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def send_alert(subject: str, message: str):
    """Send alert via webhook if configured; always logs locally."""
    alert_msg = f"PIPELINE ALERT | {subject}\n{message}"
    logger.warning(alert_msg)
    if CONFIG["ALERT_WEBHOOK"]:
        try:
            import requests
            requests.post(CONFIG["ALERT_WEBHOOK"], json={"content": alert_msg}, timeout=5)
        except Exception as e:
            logger.debug(f"Webhook failed: {e}")


def find_latest_csv(directory: str) -> str:
    """Return path to the most recently modified CSV in the watch directory."""
    csv_files = list(Path(directory).glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {directory}")
    return str(max(csv_files, key=os.path.getmtime))


def run_pipeline_with_retries() -> bool:
    """Execute the ETL pipeline with automatic retry logic."""
    logger.info("=" * 70)
    logger.info(f"Pipeline run triggered at {datetime.now().isoformat()}")

    try:
        input_path = find_latest_csv(CONFIG["WATCH_DIR"])
    except FileNotFoundError as e:
        logger.error(f"Source data missing: {e}")
        send_alert("Missing Data", str(e))
        return False

    sample = CONFIG["SAMPLE_SIZE"] if CONFIG["SAMPLE_SIZE"] > 0 else None
    start_time = datetime.now()

    for attempt in range(1, CONFIG["MAX_RETRIES"] + 1):
        try:
            logger.info(
                f"Attempt {attempt}/{CONFIG['MAX_RETRIES']} "
                f"| Source: {input_path} "
                f"| Sample: {sample or 'all'}"
            )
            result = run_paysim_etl(input_path, sample_size=sample)

            duration = (datetime.now() - start_time).total_seconds()
            if result is not None:
                logger.info(f"Pipeline completed successfully in {duration:.2f}s")
            else:
                logger.warning(
                    f"Pipeline returned None (circuit breaker may have tripped) in {duration:.2f}s"
                )
            return True

        except Exception as e:
            logger.error(f"Attempt {attempt} failed: {e}")
            if attempt < CONFIG["MAX_RETRIES"]:
                logger.info(f"Retrying in {CONFIG['RETRY_DELAY_SECONDS']}s...")
                time.sleep(CONFIG["RETRY_DELAY_SECONDS"])
            else:
                logger.critical(
                    f"All {CONFIG['MAX_RETRIES']} retries exhausted. Pipeline halted."
                )
                send_alert(
                    "Pipeline Failure",
                    f"Failed after {CONFIG['MAX_RETRIES']} attempts.\nError: {e}"
                )
                return False

    return False


def scheduled_run():
    """Thin wrapper so exceptions don't kill the scheduler."""
    try:
        run_pipeline_with_retries()
    except Exception as e:
        logger.exception(f"Unexpected scheduler error: {e}")


def main(run_once: bool = False):
    logger.info("Initializing SA Bank Data Quality Automation Runner v2.0")
    logger.info(
        f"Schedule: {CONFIG['CRON_EXPRESSION']} (Cron) "
        f"/ every {CONFIG['INTERVAL_SECONDS']}s (interval)"
    )
    logger.info(f"Retries: {CONFIG['MAX_RETRIES']} | Delay: {CONFIG['RETRY_DELAY_SECONDS']}s")
    logger.info(f"Watching: {CONFIG['WATCH_DIR']}")
    logger.info(f"Sample size per run: {CONFIG['SAMPLE_SIZE'] or 'full dataset'}")
    logger.info("=" * 70)

    logger.info("Running initial pipeline execution...")
    run_pipeline_with_retries()

    if run_once:
        logger.info("--once flag set. Exiting after single run.")
        return

    scheduler = BlockingScheduler(timezone="Africa/Johannesburg")

    if CONFIG["SCHEDULE_TYPE"].lower() == "cron":
        trigger = CronTrigger.from_crontab(CONFIG["CRON_EXPRESSION"])
    else:
        trigger = IntervalTrigger(seconds=CONFIG["INTERVAL_SECONDS"])

    scheduler.add_job(scheduled_run, trigger, id="sa_bank_dq_etl", replace_existing=True)

    logger.info("Scheduler started (Africa/Johannesburg timezone). Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("\nScheduler stopped by user.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SA Bank DQ Pipeline Automation Runner")
    parser.add_argument("--once", action="store_true", help="Run once and exit (no scheduler)")
    args = parser.parse_args()
    main(run_once=args.once)