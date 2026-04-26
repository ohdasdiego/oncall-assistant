"""
Auto-Resolver — periodically checks open incidents and resolves them
when the triggering metric has returned to normal.

Logic:
  - Runs every 5 minutes in a background thread
  - For each OPEN or INVESTIGATING incident, checks current metrics
  - Uses hysteresis: alert thresholds vs. clear thresholds to avoid flapping
  - Requires 2 consecutive clean checks before auto-resolving
  - Applies to CPU, memory, and disk incidents
"""

import os
import re
import logging
import threading
import time
import requests
from datetime import datetime

from .telegram import notify_resolved, notify_status_change
from ..models.database import (
    list_incidents, get_incident, update_incident_status, add_timeline_event
)

logger = logging.getLogger(__name__)

INFRA_MONITOR_URL = os.getenv("INFRA_MONITOR_URL", "http://localhost:5000/api/metrics")
BASE_URL = os.getenv("BASE_URL", "")
CHECK_INTERVAL = int(os.getenv("AUTO_RESOLVE_INTERVAL", "300"))  # seconds (5 min)

# Alert thresholds (same as Incident Logger)
ALERT_THRESHOLDS = {
    "cpu":    {"warn": 80, "crit": 95},
    "memory": {"warn": 85, "crit": 92},
    "disk":   {"warn": 80, "crit": 90},
}

# Clear thresholds — lower to avoid flapping
CLEAR_THRESHOLDS = {
    "cpu":    70,
    "memory": 75,
    "disk":   70,
}

# incident_id -> consecutive clean check count
_clean_counts: dict[int, int] = {}
_lock = threading.Lock()
REQUIRED_CLEAN_CHECKS = 2


def _detect_metric_type(title: str, description: str) -> str | None:
    """Detect which metric type an incident is about from its title/description."""
    text = (title + " " + (description or "")).lower()
    if re.search(r"\bcpu\b", text):
        return "cpu"
    if re.search(r"\bmem(ory)?\b", text):
        return "memory"
    if re.search(r"\bdisk\b|\bstorage\b", text):
        return "disk"
    return None


def _fetch_current_metrics() -> dict | None:
    """Fetch live metrics from Infra Monitor. Returns dict or None on failure."""
    try:
        resp = requests.get(INFRA_MONITOR_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        metrics = data.get("metrics", {})
        return {
            "cpu":    metrics.get("cpu_percent"),
            "memory": metrics.get("memory_percent"),
            "disk":   metrics.get("disk_percent"),
        }
    except Exception as e:
        logger.warning(f"Auto-resolver: failed to fetch metrics — {e}")
        return None


def _check_and_resolve():
    """Single pass: check all open incidents and auto-resolve if metrics are clear."""
    metrics = _fetch_current_metrics()
    if metrics is None:
        return

    open_incidents = [
        i for i in list_incidents(limit=100)
        if i["status"] in ("OPEN", "INVESTIGATING")
    ]

    if not open_incidents:
        with _lock:
            _clean_counts.clear()
        return

    for incident in open_incidents:
        iid = incident["id"]
        metric_type = _detect_metric_type(incident["title"], incident.get("description", ""))

        if metric_type is None:
            # Can't determine metric — skip auto-resolve
            continue

        current_value = metrics.get(metric_type)
        if current_value is None:
            continue

        clear_threshold = CLEAR_THRESHOLDS[metric_type]
        is_clear = current_value < clear_threshold

        with _lock:
            if is_clear:
                _clean_counts[iid] = _clean_counts.get(iid, 0) + 1
                count = _clean_counts[iid]
                logger.info(
                    f"Incident #{iid}: {metric_type} at {current_value:.1f}% "
                    f"(clear threshold: {clear_threshold}%) — clean check {count}/{REQUIRED_CLEAN_CHECKS}"
                )

                add_timeline_event(
                    iid, "STATUS_CHANGE",
                    f"Auto-resolver: {metric_type.upper()} at {current_value:.1f}% "
                    f"(below {clear_threshold}% clear threshold) — clean check {count}/{REQUIRED_CLEAN_CHECKS}"
                )

                if count >= REQUIRED_CLEAN_CHECKS:
                    # Auto-resolve
                    old_status = incident["status"]
                    update_incident_status(iid, "RESOLVED")
                    _clean_counts.pop(iid, None)

                    try:
                        opened = datetime.fromisoformat(incident["created_at"])
                        duration = (datetime.utcnow() - opened).total_seconds() / 60
                        notify_resolved(iid, incident["title"], duration, BASE_URL,
                                        note=f"Auto-resolved: {metric_type.upper()} returned to normal ({current_value:.1f}%)")
                    except Exception as e:
                        logger.warning(f"Auto-resolver: Telegram notify failed — {e}")

                    add_timeline_event(
                        iid, "STATUS_CHANGE",
                        f"AUTO-RESOLVED: {metric_type.upper()} sustained below {clear_threshold}% "
                        f"for {REQUIRED_CLEAN_CHECKS} consecutive checks. "
                        f"Current value: {current_value:.1f}%"
                    )
                    logger.info(f"Incident #{iid} auto-resolved ({metric_type} at {current_value:.1f}%)")
            else:
                # Metric still elevated — reset clean count
                if iid in _clean_counts:
                    logger.info(
                        f"Incident #{iid}: {metric_type} still at {current_value:.1f}% "
                        f"— resetting clean count"
                    )
                    _clean_counts.pop(iid, None)


def _run_loop():
    """Background thread main loop."""
    logger.info(f"Auto-resolver started (interval: {CHECK_INTERVAL}s, "
                f"required clean checks: {REQUIRED_CLEAN_CHECKS})")
    while True:
        try:
            _check_and_resolve()
        except Exception as e:
            logger.error(f"Auto-resolver error: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL)


def start(interval: int = None):
    """Start the auto-resolver background thread."""
    global CHECK_INTERVAL
    if interval is not None:
        CHECK_INTERVAL = interval
    t = threading.Thread(target=_run_loop, daemon=True, name="auto-resolver")
    t.start()
    return t
