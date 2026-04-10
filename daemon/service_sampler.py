"""Systemd service unit snapshot sampling for KernelIQ.

Captures the current state of all service units in one call via ``systemctl``.
The daemon's main loop invokes ``sample_services()`` each cycle; this module
does not implement its own scheduling loop.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone


def sample_services() -> list[dict[str, str]]:
    """Return one row per non-template service unit from ``systemctl list-units``.

    Runs ``systemctl list-units --type=service --all --no-pager --no-legend
    --plain``, parses unit name (``.service`` suffix removed), active state,
    and sub state. Template instances (names containing ``@``) are omitted.
    Each dict includes the same UTC timestamp and matches ``service_samples``
    columns: ``timestamp``, ``service_name``, ``active_state``, ``sub_state``.

    If ``systemctl`` is missing, times out, or exits non-zero, returns an
    empty list.

    Returns:
        List of row dicts suitable for inserting into ``service_samples``.
    """
    cmd = [
        "systemctl",
        "list-units",
        "--type=service",
        "--all",
        "--no-pager",
        "--no-legend",
        "--plain",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout:
        return []

    ts = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, str]] = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        unit = parts[0]
        if not unit.endswith(".service"):
            continue
        name = unit[: -len(".service")]
        if "@" in name:
            continue
        active_state = parts[2]
        sub_state = parts[3]
        rows.append(
            {
                "timestamp": ts,
                "service_name": name,
                "active_state": active_state,
                "sub_state": sub_state,
            }
        )

    return rows
