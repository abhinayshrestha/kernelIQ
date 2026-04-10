"""Deterministic alert detection from journal and SQLite telemetry.

Runs lightweight checks after each collection cycle. No LLM involvement;
results are written to the ``alerts`` table via :func:`db.helpers.insert_alert`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from config import (
    ALERT_CPU_SUSTAINED_SAMPLE_COUNT,
    ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT,
    ALERT_DISK_CRITICAL_PERCENT,
    ALERT_DISK_WARNING_PERCENT,
    ALERT_IOWAIT_SAMPLE_COUNT,
    ALERT_IOWAIT_THRESHOLD_PERCENT,
    ALERT_MEMORY_FREE_CRITICAL_PERCENT,
    ALERT_MEMORY_FREE_WARNING_PERCENT,
    ALERT_OOM_JOURNAL_SINCE,
    ALERT_SERVICE_FLAP_MIN_STATE_CHANGES,
    ALERT_SERVICE_FLAP_STABILITY_MINUTES,
    ALERT_SERVICE_FLAP_WINDOW_MINUTES,
    ALERT_SWAP_USED_WARNING_PERCENT,
    ALERT_ZOMBIE_MIN_COUNT,
    DAEMON_COLLECTION_INTERVAL_SEC,
)
from db.helpers import insert_alert, query

_OOM_LINE_RE = re.compile(
    r"killed process\s+(\d+)\s+\(([^)]+)\)",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 text."""
    return datetime.now(timezone.utc).isoformat()


def _unresolved_duplicate_exists(
    db_path: str, alert_type: str, subject: str
) -> bool:
    """Return True if an unresolved alert with the same type and subject exists."""
    rows = query(
        db_path,
        (
            "SELECT 1 FROM alerts WHERE alert_type = ? AND subject = ? "
            "AND resolved = 0 LIMIT 1"
        ),
        (alert_type, subject),
    )
    return bool(rows)


def detect_oom_kills(db_path: str) -> list[dict[str, Any]]:
    """Scan recent kernel journal for OOM killer lines and build alerts.

    Args:
        db_path: Path to the KernelIQ SQLite database (unused; kept for a
            uniform detector signature).

    Returns:
        Alert dicts without ``timestamp`` (added by :func:`run_alert_checks`).
    """
    _ = db_path
    try:
        completed = subprocess.run(
            [
                "journalctl",
                "-k",
                "--since",
                ALERT_OOM_JOURNAL_SINCE,
                "--no-pager",
                "-o",
                "short-precise",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []

    if completed.returncode != 0:
        return []

    out: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if "killed process" not in line.lower():
            continue
        m = _OOM_LINE_RE.search(line)
        if not m:
            continue
        pid, name = m.group(1), m.group(2).strip()
        subject = f"{name} (PID {pid})"
        out.append(
            {
                "alert_type": "OOM_KILL",
                "severity": "critical",
                "subject": subject,
                "evidence_summary": line.strip(),
            }
        )
    return out


def detect_service_failures(db_path: str) -> list[dict[str, Any]]:
    """Alert on services whose latest sample reports ``active_state`` failed.

    Args:
        db_path: Path to the KernelIQ SQLite database file.

    Returns:
        Alert dicts without ``timestamp``.
    """
    rows = query(
        db_path,
        """
        SELECT s.service_name AS service_name
        FROM service_samples s
        INNER JOIN (
            SELECT service_name, MAX(timestamp) AS max_ts
            FROM service_samples
            GROUP BY service_name
        ) latest
            ON s.service_name = latest.service_name
            AND s.timestamp = latest.max_ts
        WHERE s.active_state = 'failed' AND s.service_name IS NOT NULL
        """,
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        name = row["service_name"]
        if not name:
            continue
        out.append(
            {
                "alert_type": "SERVICE_FAILED",
                "severity": "critical",
                "subject": name,
                "evidence_summary": f"Service {name} is in failed state",
            }
        )
    return out


def detect_disk_critical(db_path: str) -> list[dict[str, Any]]:
    """Alert when any tracked mount crosses configured disk-usage thresholds.

    Args:
        db_path: Path to the KernelIQ SQLite database file.

    Returns:
        Alert dicts without ``timestamp``.
    """
    rows = query(
        db_path,
        """
        SELECT disk_usage_json
        FROM telemetry_samples
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    if not rows or not rows[0].get("disk_usage_json"):
        return []
    raw = rows[0]["disk_usage_json"]
    try:
        by_mount: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    out: list[dict[str, Any]] = []
    for mount_point, stats in by_mount.items():
        if not isinstance(stats, dict):
            continue
        pct = stats.get("percent")
        if pct is None:
            continue
        try:
            p = float(pct)
        except (TypeError, ValueError):
            continue
        if p >= ALERT_DISK_CRITICAL_PERCENT:
            sev = "critical"
        elif p >= ALERT_DISK_WARNING_PERCENT:
            sev = "warning"
        else:
            continue
        out.append(
            {
                "alert_type": "DISK_CRITICAL",
                "severity": sev,
                "subject": mount_point,
                "evidence_summary": (
                    f"Disk usage at {round(p, 1)}% on {mount_point}"
                ),
            }
        )
    return out


def detect_high_memory_pressure(db_path: str) -> list[dict[str, Any]]:
    """Alert when available RAM falls below configured free-RAM percentages.

    Args:
        db_path: Path to the KernelIQ SQLite database file.

    Returns:
        Zero or one alert dict without ``timestamp``.
    """
    rows = query(
        db_path,
        """
        SELECT mem_total_mb, mem_available_mb
        FROM telemetry_samples
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    if not rows:
        return []
    total = rows[0].get("mem_total_mb")
    avail = rows[0].get("mem_available_mb")
    if total is None or avail is None:
        return []
    try:
        t = float(total)
        a = float(avail)
    except (TypeError, ValueError):
        return []
    if t <= 0:
        return []
    free_pct = (a / t) * 100.0
    if free_pct < ALERT_MEMORY_FREE_CRITICAL_PERCENT:
        sev = "critical"
    elif free_pct < ALERT_MEMORY_FREE_WARNING_PERCENT:
        sev = "warning"
    else:
        return []
    return [
        {
            "alert_type": "MEMORY_CRITICAL",
            "severity": sev,
            "subject": "system",
            "evidence_summary": (
                f"Only {a}MB available out of {t}MB "
                f"({round(free_pct, 1)}% free)"
            ),
        }
    ]


def detect_high_swap(db_path: str) -> list[dict[str, Any]]:
    """Alert when swap usage is at or above the configured percent of total swap.

    Args:
        db_path: Path to the KernelIQ SQLite database file.

    Returns:
        Zero or one alert dict without ``timestamp``.
    """
    rows = query(
        db_path,
        """
        SELECT swap_total_mb, swap_used_mb
        FROM telemetry_samples
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    if not rows:
        return []
    total = rows[0].get("swap_total_mb")
    used = rows[0].get("swap_used_mb")
    if total is None or used is None:
        return []
    try:
        t = float(total)
        u = float(used)
    except (TypeError, ValueError):
        return []
    if t <= 0:
        return []
    swap_pct = (u / t) * 100.0
    if swap_pct < ALERT_SWAP_USED_WARNING_PERCENT:
        return []
    return [
        {
            "alert_type": "SWAP_HIGH",
            "severity": "warning",
            "subject": "system",
            "evidence_summary": (
                f"Swap usage at {round(swap_pct, 1)}% ({u}MB / {t}MB)"
            ),
        }
    ]


def detect_cpu_sustained(db_path: str) -> list[dict[str, Any]]:
    """Alert when the last N telemetry samples all show CPU at or above the threshold."""
    n = ALERT_CPU_SUSTAINED_SAMPLE_COUNT
    rows = query(
        db_path,
        """
        SELECT cpu_percent FROM telemetry_samples
        ORDER BY timestamp DESC LIMIT ?
        """,
        (n,),
    )
    if len(rows) < n:
        return []
    thr = ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT
    for row in rows:
        cpu = row.get("cpu_percent")
        if cpu is None or float(cpu) < thr:
            return []
    span_sec = n * DAEMON_COLLECTION_INTERVAL_SEC
    return [
        {
            "alert_type": "CPU_SUSTAINED",
            "severity": "critical",
            "subject": "system",
            "evidence_summary": (
                f"CPU above {thr}% for last {n} consecutive samples "
                f"({span_sec}s at {DAEMON_COLLECTION_INTERVAL_SEC}s interval)"
            ),
        }
    ]


def detect_iowait_high(db_path: str) -> list[dict[str, Any]]:
    """Alert when the last N samples all show iowait at or above the threshold."""
    n = ALERT_IOWAIT_SAMPLE_COUNT
    rows = query(
        db_path,
        """
        SELECT iowait FROM telemetry_samples
        ORDER BY timestamp DESC LIMIT ?
        """,
        (n,),
    )
    if len(rows) < n:
        return []
    thr = ALERT_IOWAIT_THRESHOLD_PERCENT
    for row in rows:
        iw = row.get("iowait")
        if iw is None or float(iw) < thr:
            return []
    return [
        {
            "alert_type": "IOWAIT_HIGH",
            "severity": "warning",
            "subject": "system",
            "evidence_summary": (
                f"iowait above {thr}% for last {n} consecutive samples — disk IO "
                "bottleneck likely"
            ),
        }
    ]


def detect_zombie_accumulation(db_path: str) -> list[dict[str, Any]]:
    """Alert when the latest process snapshot has at least N zombie processes."""
    latest_rows = query(
        db_path,
        "SELECT MAX(timestamp) AS latest FROM process_samples",
    )
    if not latest_rows or latest_rows[0].get("latest") is None:
        return []
    latest_ts = latest_rows[0]["latest"]
    cnt_rows = query(
        db_path,
        """
        SELECT COUNT(*) AS cnt FROM process_samples
        WHERE timestamp = ? AND (state = 'Z' OR state = 'zombie')
        """,
        (latest_ts,),
    )
    if not cnt_rows:
        return []
    cnt = cnt_rows[0].get("cnt")
    try:
        n = int(cnt) if cnt is not None else 0
    except (TypeError, ValueError):
        return []
    if n < ALERT_ZOMBIE_MIN_COUNT:
        return []
    return [
        {
            "alert_type": "ZOMBIE_ACCUMULATION",
            "severity": "warning",
            "subject": "system",
            "evidence_summary": (
                f"{n} zombie processes detected at {latest_ts}"
            ),
        }
    ]


def detect_service_flapping(db_path: str) -> list[dict[str, Any]]:
    """Alert when a service changes active_state often within the lookback window."""
    win = ALERT_SERVICE_FLAP_WINDOW_MINUTES
    rows = query(
        db_path,
        f"""
        SELECT service_name, active_state, timestamp
        FROM service_samples
        WHERE timestamp > datetime('now', '-{win} minutes')
        ORDER BY service_name ASC, timestamp ASC
        """,
    )
    by_svc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        name = row.get("service_name")
        if not name:
            continue
        by_svc[str(name)].append(row)

    out: list[dict[str, Any]] = []
    for svc, svc_rows in by_svc.items():
        if len(svc_rows) < 2:
            continue
        changes = 0
        prev = svc_rows[0].get("active_state")
        for r in svc_rows[1:]:
            cur = r.get("active_state")
            if cur != prev:
                changes += 1
                prev = cur
        if changes >= ALERT_SERVICE_FLAP_MIN_STATE_CHANGES:
            # Skip alert if the last state change was too long ago (stable now).
            last_change_ts = None
            prev_state = svc_rows[0].get("active_state")
            for r in svc_rows[1:]:
                cur_state = r.get("active_state")
                if cur_state != prev_state:
                    last_change_ts = r.get("timestamp")
                    prev_state = cur_state

            if last_change_ts:
                try:
                    raw = last_change_ts
                    if raw.endswith("Z"):
                        raw = raw[:-1] + "+00:00"
                    last_dt = datetime.fromisoformat(raw)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    cutoff = datetime.now(timezone.utc) - timedelta(
                        minutes=ALERT_SERVICE_FLAP_STABILITY_MINUTES
                    )
                    if last_dt < cutoff:
                        continue
                except (ValueError, AttributeError):
                    pass

            out.append(
                {
                    "alert_type": "SERVICE_FLAPPING",
                    "severity": "critical",
                    "subject": svc,
                    "evidence_summary": (
                        f"{svc} changed state {changes} times in last {win} minutes — "
                        "crashlooping likely"
                    ),
                }
            )
    return out


def send_notification(
    alert_type: str,
    severity: str,
    subject: str,
    evidence_summary: str,
) -> None:
    """Desktop notification via ``notify-send``; never raises."""
    try:
        sev = (severity or "").lower()
        if sev == "critical":
            title = "🔴 KernelIQ — Critical"
            urgency_flag = "--urgency=critical"
        elif sev == "warning":
            title = "🟡 KernelIQ — Warning"
            urgency_flag = "--urgency=normal"
        elif sev == "info":
            title = "🔵 KernelIQ — Info"
            urgency_flag = "--urgency=normal"
        else:
            title = "🔵 KernelIQ"
            urgency_flag = "--urgency=normal"
        body = f"{subject} — {evidence_summary}"
        if len(body) > 100:
            body = body[:100]
        subprocess.run(
            [
                "notify-send",
                title,
                body,
                urgency_flag,
                "--expire-time=10000",
            ],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def run_alert_checks(db_path: str) -> None:
    """Run all detectors and insert new alerts, skipping unresolved duplicates.

    Each detector may return multiple alerts. An alert is skipped if an
    unresolved row with the same ``alert_type`` and ``subject`` already exists.
    All inserted rows share one UTC timestamp for the run.

    Args:
        db_path: Path to the KernelIQ SQLite database file.
    """
    detectors = (
        detect_oom_kills,
        detect_service_failures,
        detect_disk_critical,
        detect_high_memory_pressure,
        detect_high_swap,
        detect_cpu_sustained,
        detect_iowait_high,
        detect_zombie_accumulation,
        detect_service_flapping,
    )
    ts = _utc_now_iso()
    for detector in detectors:
        for partial in detector(db_path):
            alert_type = partial["alert_type"]
            subject = partial["subject"]
            if _unresolved_duplicate_exists(db_path, alert_type, subject):
                continue
            row = {
                "timestamp": ts,
                **partial,
            }
            insert_alert(db_path, row)
            send_notification(
                partial["alert_type"],
                partial["severity"],
                partial["subject"],
                partial["evidence_summary"],
            )
