"""Reusable SQLite helpers for KernelIQ.

Each function accepts ``db_path`` and opens its own connection so callers
never share connections across threads. Use :func:`get_connection` only when
you manage the connection lifecycle yourself.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

_TELEMETRY_COLUMNS = (
    "timestamp",
    "cpu_percent",
    "load_avg_1",
    "load_avg_5",
    "load_avg_15",
    "iowait",
    "mem_total_mb",
    "mem_used_mb",
    "mem_available_mb",
    "swap_total_mb",
    "swap_used_mb",
    "disk_usage_json",
    "disk_io_json",
    "net_json",
    "temp_json",
    "gpu_json",
    "psi_cpu_some",
    "psi_cpu_full",
    "psi_memory_some",
    "psi_memory_full",
    "psi_io_some",
    "psi_io_full",
    "cpu_per_core_json",
    "mem_buffers_mb",
    "mem_cached_mb",
    "net_connections_established",
    "net_connections_syn_recv",
    "net_connections_time_wait",
    "net_connections_close_wait",
    "net_connections_total",
    "page_faults_minor",
    "page_faults_major",
)

_PROCESS_COLUMNS = (
    "timestamp",
    "pid",
    "name",
    "cmdline",
    "cpu_percent",
    "mem_rss_mb",
    "mem_percent",
    "state",
    "io_read_bytes",
    "io_write_bytes",
)

_SERVICE_COLUMNS = (
    "timestamp",
    "service_name",
    "active_state",
    "sub_state",
)

_INVESTIGATION_COLUMNS = (
    "session_id",
    "timestamp",
    "question",
    "step_number",
    "command_type",
    "command_run",
    "output_summary",
    "reasoning",
)

_DIAGNOSIS_COLUMNS = (
    "session_id",
    "timestamp",
    "question",
    "observation",
    "evidence_json",
    "action_proposed",
    "command_proposed",
    "confidence",
)

_ALERT_COLUMNS = (
    "timestamp",
    "alert_type",
    "severity",
    "subject",
    "evidence_summary",
    "resolution_type",
)

_FEEDBACK_VALUES = frozenset({"correct", "wrong", "partial"})


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection to ``db_path`` with row factory enabled.

    ``sqlite3.Row`` rows behave like dicts for column access and can be
    converted with ``dict(row)``.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Open connection. The caller must :meth:`~sqlite3.Connection.close`
        it when finished.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def insert_telemetry(db_path: str, data: dict[str, Any]) -> None:
    """Insert one row into ``telemetry_samples``.

    ``data`` keys must match schema column names (except ``id``). ``timestamp``
    must be an ISO 8601 string. JSON columns are stored as TEXT; pass
    already-serialized strings.

    Args:
        db_path: Path to the SQLite database file.
        data: Mapping of column names to values.
    """
    if "timestamp" not in data or data["timestamp"] is None:
        raise ValueError("data must include non-null 'timestamp'")
    cols = _TELEMETRY_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO telemetry_samples ({col_list}) VALUES ({placeholders})"
    params = tuple(data.get(c) for c in cols)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def insert_process_samples(db_path: str, samples: list[dict[str, Any]]) -> None:
    """Insert many rows into ``process_samples`` in a single transaction.

    Each dict must provide: ``timestamp``, ``pid``, ``name``, ``cmdline``,
    ``cpu_percent``, ``mem_rss_mb``, ``mem_percent``, ``state``,
    ``io_read_bytes``, ``io_write_bytes`` (may be ``None``).

    Args:
        db_path: Path to the SQLite database file.
        samples: Row dicts in insert order.
    """
    if not samples:
        return
    cols = _PROCESS_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO process_samples ({col_list}) VALUES ({placeholders})"
    rows = [tuple(s[c] for c in cols) for s in samples]
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


def insert_service_samples(db_path: str, samples: list[dict[str, Any]]) -> None:
    """Insert many rows into ``service_samples`` in a single transaction.

    Each dict must provide: ``timestamp``, ``service_name``, ``active_state``,
    ``sub_state``.

    Args:
        db_path: Path to the SQLite database file.
        samples: Row dicts in insert order.
    """
    if not samples:
        return
    cols = _SERVICE_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO service_samples ({col_list}) VALUES ({placeholders})"
    rows = [tuple(s[c] for c in cols) for s in samples]
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


def insert_investigation_log(db_path: str, data: dict[str, Any]) -> None:
    """Insert one row into ``investigation_logs``.

    Expected keys: ``session_id``, ``timestamp``, ``question``,
    ``step_number``, ``command_type``, ``command_run``, ``output_summary``,
    ``reasoning``.

    Args:
        db_path: Path to the SQLite database file.
        data: Mapping of column names to values.
    """
    cols = _INVESTIGATION_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO investigation_logs ({col_list}) VALUES ({placeholders})"
    params = tuple(data[c] for c in cols)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def insert_diagnosis(db_path: str, data: dict[str, Any]) -> int:
    """Insert one row into ``diagnoses`` and return the new row id.

    Expected keys: ``session_id``, ``timestamp``, ``question``,
    ``observation``, ``evidence_json``, ``action_proposed``,
    ``command_proposed``, ``confidence``. ``user_confirmed`` and
    ``user_feedback`` are left unset (NULL).

    Args:
        db_path: Path to the SQLite database file.
        data: Mapping of column names to values.

    Returns:
        SQLite ``lastrowid`` for the inserted diagnosis.
    """
    cols = _DIAGNOSIS_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO diagnoses ({col_list}) VALUES ({placeholders})"
    params = tuple(data[c] for c in cols)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_feedback(db_path: str, diagnosis_id: int, feedback: str) -> None:
    """Set ``user_feedback`` on a diagnosis row.

    Args:
        db_path: Path to the SQLite database file.
        diagnosis_id: Primary key of the ``diagnoses`` row.
        feedback: One of ``\"correct\"``, ``\"wrong\"``, ``\"partial\"``.
    """
    if feedback not in _FEEDBACK_VALUES:
        raise ValueError(
            f"feedback must be one of {sorted(_FEEDBACK_VALUES)}, got {feedback!r}"
        )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE diagnoses SET user_feedback = ? WHERE id = ?",
            (feedback, diagnosis_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_alert(db_path: str, data: dict[str, Any]) -> None:
    """Insert one row into ``alerts``.

    Expected keys: ``timestamp``, ``alert_type``, ``severity``, ``subject``,
    ``evidence_summary``. Optional ``resolution_type`` (defaults to ``None``).
    ``resolved`` defaults to ``0`` in the schema.

    Args:
        db_path: Path to the SQLite database file.
        data: Mapping of column names to values.
    """
    cols = _ALERT_COLUMNS
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO alerts ({col_list}) VALUES ({placeholders})"
    params = tuple(
        data.get("resolution_type") if c == "resolution_type" else data[c]
        for c in cols
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def resolve_alert(db_path: str, alert_id: int) -> None:
    """Mark an alert resolved with the current UTC time as ISO 8601 text.

    Sets ``resolved = 1`` and ``resolved_at`` to
    :meth:`datetime.now(timezone.utc).isoformat`.

    Args:
        db_path: Path to the SQLite database file.
        alert_id: Primary key of the ``alerts`` row.
    """
    resolved_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE alerts
            SET resolved = 1,
                resolved_at = ?,
                resolution_type = 'manual'
            WHERE id = ?
            """,
            (resolved_at, alert_id),
        )
        conn.commit()
    finally:
        conn.close()


def auto_resolve_alert(
    db_path: str, alert_id: int, reason: str = ""
) -> None:
    """Mark an alert resolved automatically and optionally append a resolution note.

    Sets ``resolved``, ``resolved_at``, ``resolution_type = 'auto'``, and
    appends `` | Auto-resolved: <reason>`` to ``evidence_summary`` when
    ``reason`` is non-empty.

    Args:
        db_path: Path to the SQLite database file.
        alert_id: Primary key of the ``alerts`` row.
        reason: Short explanation appended to ``evidence_summary`` when set.
    """
    resolved_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE alerts
            SET resolved = 1,
                resolved_at = ?,
                resolution_type = 'auto',
                evidence_summary = evidence_summary ||
                CASE WHEN ? != ''
                     THEN ' | Auto-resolved: ' || ?
                     ELSE '' END
            WHERE id = ?
            """,
            (resolved_at, reason, reason, alert_id),
        )
        conn.commit()
    finally:
        conn.close()


def bulk_resolve_all(db_path: str, resolution_type: str = "auto") -> int:
    """Resolve every alert that is still open in one update.

    Args:
        db_path: Path to the SQLite database file.
        resolution_type: Stored ``resolution_type`` value (default ``\"auto\"``).

    Returns:
        Number of rows updated (``cursor.rowcount``).
    """
    resolved_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE alerts
            SET resolved = 1,
                resolved_at = ?,
                resolution_type = ?
            WHERE resolved = 0
            """,
            (resolved_at, resolution_type),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def get_unresolved_alerts(db_path: str) -> list[dict[str, Any]]:
    """Return all unresolved alerts oldest-first.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Row dicts with id, timestamp, alert_type, severity, subject,
        evidence_summary, resolved, resolved_at, resolution_type.
    """
    return query(
        db_path,
        """
        SELECT id, timestamp, alert_type, severity,
               subject, evidence_summary, resolved,
               resolved_at, resolution_type
        FROM alerts
        WHERE resolved = 0
        ORDER BY timestamp ASC
        """,
    )


def check_alert_condition_resolved(
    db_path: str, alert: dict[str, Any]
) -> tuple[bool, str]:
    """Return whether telemetry indicates the alert condition has cleared.

    Args:
        db_path: Path to the SQLite database file.
        alert: One row dict from :func:`get_unresolved_alerts`.

    Returns:
        ``(True, reason)`` if the condition is gone, else ``(False, \"\")``.
    """
    alert_type = str(alert.get("alert_type") or "").strip()
    subject = str(alert.get("subject") or "").strip()

    if alert_type == "CPU_SUSTAINED":
        rows = query(
            db_path,
            """
            SELECT cpu_percent FROM telemetry_samples
            ORDER BY timestamp DESC LIMIT 3
            """,
        )
        for row in rows:
            cpu = row.get("cpu_percent")
            if cpu is not None and float(cpu) < 80.0:
                return True, "CPU now below 80%"
        return False, ""

    if alert_type == "IOWAIT_HIGH":
        rows = query(
            db_path,
            """
            SELECT iowait FROM telemetry_samples
            ORDER BY timestamp DESC LIMIT 3
            """,
        )
        for row in rows:
            iw = row.get("iowait")
            if iw is not None and float(iw) < 20.0:
                return True, "iowait now below 20%"
        return False, ""

    if alert_type == "MEMORY_CRITICAL":
        rows = query(
            db_path,
            """
            SELECT mem_total_mb, mem_available_mb
            FROM telemetry_samples
            ORDER BY timestamp DESC LIMIT 1
            """,
        )
        if not rows:
            return False, ""
        total = rows[0].get("mem_total_mb")
        avail = rows[0].get("mem_available_mb")
        if total is None or avail is None:
            return False, ""
        try:
            t = float(total)
            a = float(avail)
        except (TypeError, ValueError):
            return False, ""
        if t <= 0:
            return False, ""
        if (a / t) > 0.10:
            return True, "RAM now above 10% available"
        return False, ""

    if alert_type == "SWAP_HIGH":
        rows = query(
            db_path,
            """
            SELECT swap_total_mb, swap_used_mb
            FROM telemetry_samples
            ORDER BY timestamp DESC LIMIT 1
            """,
        )
        if not rows:
            return False, ""
        total = rows[0].get("swap_total_mb")
        used = rows[0].get("swap_used_mb")
        if total is None or used is None:
            return False, ""
        try:
            t = float(total)
            u = float(used)
        except (TypeError, ValueError):
            return False, ""
        if t <= 0:
            return False, ""
        swap_pct = (u / t) * 100.0
        if swap_pct < 80.0:
            return True, "Swap now below 80%"
        return False, ""

    if alert_type == "DISK_CRITICAL":
        rows = query(
            db_path,
            """
            SELECT disk_usage_json FROM telemetry_samples
            ORDER BY timestamp DESC LIMIT 1
            """,
        )
        if not rows or not rows[0].get("disk_usage_json"):
            return False, ""
        raw = rows[0]["disk_usage_json"]
        try:
            by_mount: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False, ""
        stats = by_mount.get(subject)
        if not isinstance(stats, dict):
            return False, ""
        pct = stats.get("percent")
        if pct is None:
            return False, ""
        try:
            p = float(pct)
        except (TypeError, ValueError):
            return False, ""
        if p < 85.0:
            return True, f"Disk now below 85% on {subject}"
        return False, ""

    if alert_type == "SERVICE_FAILED":
        rows = query(
            db_path,
            """
            SELECT active_state FROM service_samples
            WHERE service_name = ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (subject,),
        )
        if not rows:
            return False, ""
        state = rows[0].get("active_state")
        if state is not None and str(state) != "failed":
            return True, f"{subject} is now active"
        return False, ""

    if alert_type == "SERVICE_FLAPPING":
        rows = query(
            db_path,
            """
            SELECT service_name, active_state, timestamp
            FROM service_samples
            WHERE service_name = ?
            AND timestamp > datetime('now', '-5 minutes')
            ORDER BY timestamp ASC
            """,
            (subject,),
        )
        if not rows:
            return False, ""
        if len(rows) < 2:
            return True, f"{subject} is now stable"
        changes = 0
        prev = rows[0].get("active_state")
        for row in rows[1:]:
            cur_state = row.get("active_state")
            if cur_state != prev:
                changes += 1
                prev = cur_state
        if changes < 3:
            return True, f"{subject} is now stable"
        return False, ""

    if alert_type == "ZOMBIE_ACCUMULATION":
        rows = query(
            db_path,
            """
            SELECT COUNT(*) as cnt FROM process_samples
            WHERE timestamp = (SELECT MAX(timestamp) FROM process_samples)
            AND state = 'Z'
            """,
        )
        if not rows:
            return False, ""
        cnt = rows[0].get("cnt")
        try:
            n = int(cnt) if cnt is not None else 0
        except (TypeError, ValueError):
            n = 0
        if n < 5:
            return True, "Zombie count now below 5"
        return False, ""

    if alert_type == "OOM_KILL":
        return True, "OOM kill event has passed"

    return False, ""


def query(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Run a read-only SQL statement and return rows as plain dicts.

    Intended for parameterized ``SELECT`` statements used by the SQL
    executor. Uses :func:`get_connection` so rows are ``sqlite3.Row``
    instances converted with :class:`dict`.

    Args:
        db_path: Path to the SQLite database file.
        sql: SQL string (typically ``SELECT``).
        params: Query parameters bound to ``?`` placeholders.

    Returns:
        List of one dict per result row (column names to values).
    """
    conn = get_connection(db_path)
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
