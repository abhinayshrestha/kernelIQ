"""SQLite schema for KernelIQ: telemetry, investigations, diagnoses, and alerts."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _project_root() -> Path:
    """Return the KernelIQ repository root (parent of the ``db`` package)."""
    return Path(__file__).resolve().parent.parent


def get_db_path() -> str:
    """Return the absolute path to the KernelIQ SQLite database file.

    The file is ``kerneliq.db`` at the project root (the directory that
    contains ``db/``, ``daemon/``, ``main.py``, etc.), not the shell cwd.

    Returns:
        Absolute path string, e.g. ``/home/user/kernelIQ/kerneliq.db``.
    """
    return str(_project_root() / "kerneliq.db")


def create_tables(db_path: str) -> None:
    """Create all KernelIQ tables and indexes if they do not already exist.

    Connects to SQLite at ``db_path``, enables WAL journal mode for
    concurrent readers, runs ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` statements from the project schema,
    commits, and closes the connection.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telemetry_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cpu_percent REAL,
                load_avg_1 REAL,
                load_avg_5 REAL,
                load_avg_15 REAL,
                iowait REAL,
                mem_total_mb REAL,
                mem_used_mb REAL,
                mem_available_mb REAL,
                swap_total_mb REAL,
                swap_used_mb REAL,
                disk_usage_json TEXT,
                disk_io_json TEXT,
                net_json TEXT,
                temp_json TEXT,
                gpu_json TEXT
            );

            CREATE TABLE IF NOT EXISTS process_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                pid INTEGER,
                name TEXT,
                cmdline TEXT,
                cpu_percent REAL,
                mem_rss_mb REAL,
                mem_percent REAL,
                state TEXT
            );

            CREATE TABLE IF NOT EXISTS service_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                service_name TEXT,
                active_state TEXT,
                sub_state TEXT
            );

            CREATE TABLE IF NOT EXISTS investigation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                question TEXT,
                step_number INTEGER,
                command_type TEXT,
                command_run TEXT,
                output_summary TEXT,
                reasoning TEXT
            );

            CREATE TABLE IF NOT EXISTS diagnoses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                question TEXT,
                observation TEXT,
                evidence_json TEXT,
                action_proposed TEXT,
                command_proposed TEXT,
                confidence REAL,
                user_confirmed INTEGER,
                user_feedback TEXT
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alert_type TEXT,
                severity TEXT,
                subject TEXT,
                evidence_summary TEXT,
                resolved INTEGER DEFAULT 0,
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_samples_timestamp
                ON telemetry_samples(timestamp);

            CREATE INDEX IF NOT EXISTS idx_process_samples_timestamp
                ON process_samples(timestamp);
            CREATE INDEX IF NOT EXISTS idx_process_samples_pid
                ON process_samples(pid);

            CREATE INDEX IF NOT EXISTS idx_service_samples_timestamp
                ON service_samples(timestamp);
            CREATE INDEX IF NOT EXISTS idx_service_samples_service_name
                ON service_samples(service_name);

            CREATE INDEX IF NOT EXISTS idx_investigation_logs_session_id
                ON investigation_logs(session_id);
            CREATE INDEX IF NOT EXISTS idx_investigation_logs_timestamp
                ON investigation_logs(timestamp);

            CREATE INDEX IF NOT EXISTS idx_diagnoses_timestamp
                ON diagnoses(timestamp);
            CREATE INDEX IF NOT EXISTS idx_diagnoses_session_id
                ON diagnoses(session_id);

            CREATE INDEX IF NOT EXISTS idx_alerts_timestamp
                ON alerts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_alerts_resolved
                ON alerts(resolved);
            """
        )
        try:
            conn.execute(
                "ALTER TABLE alerts ADD COLUMN resolution_type TEXT"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_cpu_some REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_cpu_full REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_memory_some REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_memory_full REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_io_some REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN psi_io_full REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN cpu_per_core_json TEXT"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN mem_buffers_mb REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN mem_cached_mb REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN "
                "net_connections_established INTEGER"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN "
                "net_connections_syn_recv INTEGER"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN "
                "net_connections_time_wait INTEGER"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN "
                "net_connections_close_wait INTEGER"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN "
                "net_connections_total INTEGER"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN page_faults_minor REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE telemetry_samples ADD COLUMN page_faults_major REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute(
                "ALTER TABLE process_samples ADD COLUMN io_read_bytes REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE process_samples ADD COLUMN io_write_bytes REAL"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

        conn.commit()
    finally:
        conn.close()
