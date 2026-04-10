"""Main daemon loop for KernelIQ: periodic collection and SQLite writes.

Runs as a long-lived process, invoking system, process, and service
collectors each cycle. Failures in one collector are isolated so the
others still run.
"""

from __future__ import annotations

import sys
import time
import traceback

from config import DAEMON_COLLECTION_INTERVAL_SEC
from db.helpers import insert_process_samples, insert_service_samples, insert_telemetry
from db.schema import create_tables, get_db_path
from daemon.alerter import run_alert_checks
from daemon.collector import collect_system_snapshot
from daemon.process_sampler import sample_top_processes
from daemon.service_sampler import sample_services


def run_collection_cycle(db_path: str) -> None:
    """Run one collection round: telemetry, top processes, and services.

    Each collector is wrapped in its own exception handler so one failure
    does not prevent the rest. Errors are printed to stderr.

    Args:
        db_path: Path to the KernelIQ SQLite database file.
    """
    try:
        snapshot = collect_system_snapshot()
        insert_telemetry(db_path, snapshot)
    except Exception as exc:
        print(f"KernelIQ: telemetry collection failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    try:
        processes = sample_top_processes(top_n=10)
        insert_process_samples(db_path, processes)
    except Exception as exc:
        print(f"KernelIQ: process sampling failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    try:
        services = sample_services()
        insert_service_samples(db_path, services)
    except Exception as exc:
        print(f"KernelIQ: service sampling failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    try:
        run_alert_checks(db_path)
    except Exception as exc:
        print(f"KernelIQ: alert checks failed: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


def start_daemon(interval: int | None = None) -> None:
    """Initialize the database and run the collection loop until interrupted.

    Ensures tables exist, prints a startup line, then repeatedly runs
    :func:`run_collection_cycle` and sleeps between cycles.
    ``KeyboardInterrupt`` (Ctrl+C) exits cleanly with a short message.

    Args:
        interval: Seconds to wait between collection cycles. When ``None``,
            uses :data:`config.DAEMON_COLLECTION_INTERVAL_SEC`.
    """
    wait_sec = (
        DAEMON_COLLECTION_INTERVAL_SEC if interval is None else interval
    )
    db_path = get_db_path()
    create_tables(db_path)
    print(f"KernelIQ daemon started — collecting every {wait_sec}s")

    try:
        while True:
            run_collection_cycle(db_path)
            time.sleep(wait_sec)
    except KeyboardInterrupt:
        print("KernelIQ daemon stopped")


if __name__ == "__main__":
    start_daemon()
