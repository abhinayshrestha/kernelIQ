"""Prune historical SQLite rows according to KernelIQ retention policy.

Only this scheduler-driven module performs ``DELETE`` operations; the model
and investigation path never remove data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from db.schema import get_db_path


def _utc_cutoff_iso(days: int) -> str:
    """Return UTC ``now - days`` as an ISO 8601 string for TEXT comparison."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def purge_old_rows(db_path: str) -> dict[str, int]:
    """Delete rows older than the configured retention windows.

    Cutoffs are computed in UTC and compared to each table's ``timestamp``
    column as ISO 8601 text (lexicographic order matches time order for
    standard ISO formats).

    Policy:

    * ``telemetry_samples``, ``process_samples``, ``service_samples``:
      ``timestamp`` older than 7 days.
    * ``investigation_logs``: older than 14 days.
    * ``diagnoses``: older than 90 days.
    * ``alerts``: ``resolved = 1`` and ``timestamp`` older than 30 days.

    Runs ``VACUUM`` after deletes to reclaim file space.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Mapping of table name to number of rows deleted (including zero).
    """
    cut_7 = _utc_cutoff_iso(7)
    cut_14 = _utc_cutoff_iso(14)
    cut_30 = _utc_cutoff_iso(30)
    cut_90 = _utc_cutoff_iso(90)

    results: dict[str, int] = {
        "telemetry_samples": 0,
        "process_samples": 0,
        "service_samples": 0,
        "investigation_logs": 0,
        "diagnoses": 0,
        "alerts": 0,
    }

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM telemetry_samples WHERE timestamp < ?",
            (cut_7,),
        )
        results["telemetry_samples"] = max(cur.rowcount, 0)

        cur = conn.execute(
            "DELETE FROM process_samples WHERE timestamp < ?",
            (cut_7,),
        )
        results["process_samples"] = max(cur.rowcount, 0)

        cur = conn.execute(
            "DELETE FROM service_samples WHERE timestamp < ?",
            (cut_7,),
        )
        results["service_samples"] = max(cur.rowcount, 0)

        cur = conn.execute(
            "DELETE FROM investigation_logs WHERE timestamp < ?",
            (cut_14,),
        )
        results["investigation_logs"] = max(cur.rowcount, 0)

        cur = conn.execute(
            "DELETE FROM diagnoses WHERE timestamp < ?",
            (cut_90,),
        )
        results["diagnoses"] = max(cur.rowcount, 0)

        cur = conn.execute(
            "DELETE FROM alerts WHERE resolved = 1 AND timestamp < ?",
            (cut_30,),
        )
        results["alerts"] = max(cur.rowcount, 0)

        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    return results


def log_purge_results(results: dict[str, int]) -> None:
    """Print one summary line per table that had deletions.

    Tables with zero deleted rows produce no output.

    Args:
        results: Mapping from :func:`purge_old_rows`.
    """
    for table, count in results.items():
        if count > 0:
            print(f"Purged {count} rows from {table}")


if __name__ == "__main__":
    out = purge_old_rows(get_db_path())
    log_purge_results(out)
