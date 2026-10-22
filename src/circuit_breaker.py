# src/circuit_breaker.py
"""
Data Circuit Breaker for SA Bank DQ Pipeline.

States:
  CLOSED    - Normal operation, data flowing
  OPEN      - Too many failures; pipeline halted
  HALF_OPEN - Recovery window: next batch is a trial; resets to CLOSED on success
"""
import json
from datetime import datetime, timedelta
from pathlib import Path


STATE_FILE = "reports/circuit_state.json"


class DataCircuitBreaker:
    """
    Circuit breaker that pauses the pipeline when the data failure rate
    exceeds a configured threshold.

    Thresholds
    ----------
    failure_threshold : int
        Number of consecutive bad batches before the circuit trips to OPEN.
    max_failure_rate : float
        Percentage of bad records that triggers a failure (default 8%).
    recovery_seconds : int
        How long (in seconds) the breaker stays OPEN before moving to HALF_OPEN.
        Default is 300 s (5 minutes).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        max_failure_rate: float = 8.0,
        recovery_seconds: int = 300,
        state_file: str = STATE_FILE,
    ):
        self.failure_threshold = failure_threshold
        self.max_failure_rate = max_failure_rate
        self.recovery_seconds = recovery_seconds
        self.state_file = Path(state_file)

        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time: datetime | None = None

        self._load_state()

    def check_batch(self, bad_count: int, total: int) -> bool:
        """
        Evaluate a processed batch.

        Returns
        -------
        True  - batch accepted; pipeline should continue
        False - batch rejected; pipeline should halt or quarantine
        """
        self._maybe_transition_to_half_open()

        failure_rate = (bad_count / total * 100) if total > 0 else 0.0

        if self.state == "HALF_OPEN":
            if failure_rate <= self.max_failure_rate:
                self._reset()
                print(f"HALF_OPEN -> CLOSED | Trial batch passed ({failure_rate:.1f}% bad).")
                return True
            else:
                self.state = "OPEN"
                self.last_failure_time = datetime.now()
                self._save_state()
                print(f"HALF_OPEN -> OPEN | Trial failed ({failure_rate:.1f}% bad). Still tripped.")
                return False

        if failure_rate > self.max_failure_rate:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            print(
                f"Bad batch: {failure_rate:.1f}% (threshold {self.max_failure_rate}%) "
                f"| Consecutive failures: {self.failure_count}/{self.failure_threshold}"
            )
            if self.failure_count >= self.failure_threshold:
                self.state = "OPEN"
                self._save_state()
                print(f"CIRCUIT TRIPPED -> OPEN | {self.failure_count} consecutive bad batches.")
                return False
            self._save_state()
            return False
        else:
            self._reset()
            print(f"Circuit CLOSED | Batch quality good ({failure_rate:.1f}% bad).")
            return True

    def _maybe_transition_to_half_open(self):
        """Move OPEN -> HALF_OPEN once the recovery window has elapsed."""
        if self.state == "OPEN" and self.last_failure_time is not None:
            elapsed = (datetime.now() - self.last_failure_time).total_seconds()
            if elapsed >= self.recovery_seconds:
                self.state = "HALF_OPEN"
                self._save_state()
                print(
                    f"OPEN -> HALF_OPEN | Recovery window elapsed "
                    f"({elapsed:.0f}s >= {self.recovery_seconds}s). Running trial batch."
                )

    def reset(self):
        """Manually reset the circuit to CLOSED (e.g., after fixing upstream data)."""
        self._reset()
        print("Circuit manually reset -> CLOSED.")

    def _reset(self):
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = None
        self._save_state()

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "max_failure_rate": self.max_failure_rate,
            "recovery_seconds": self.recovery_seconds,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
        }

    def _save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception as e:
            print(f"Could not persist circuit state: {e}")

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            self.state = data.get("state", "CLOSED")
            self.failure_count = data.get("failure_count", 0)
            raw_ts = data.get("last_failure_time")
            self.last_failure_time = datetime.fromisoformat(raw_ts) if raw_ts else None
            print(f"Loaded circuit state: {self.state} (failures: {self.failure_count})")
        except Exception as e:
            print(f"Could not load circuit state: {e}")