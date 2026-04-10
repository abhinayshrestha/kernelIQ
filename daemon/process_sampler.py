"""Per-process snapshot sampling for KernelIQ.

Collects the top CPU and top RSS processes in a single call, including optional
per-process I/O byte counters (``io_read_bytes``, ``io_write_bytes``) when
``psutil`` exposes them. The daemon's main loop invokes
``sample_top_processes()`` each cycle; this module does not implement its own
scheduling loop.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import psutil

_MB = 1048576

_PROCESS_ATTRS = [
    "pid",
    "name",
    "cmdline",
    "cpu_percent",
    "memory_info",
    "memory_percent",
    "status",
]


def _status_to_state(status: object | None) -> str | None:
    """Normalize psutil process status to a string for the ``state`` column."""
    if status is None:
        return None
    if isinstance(status, str):
        return status
    name = getattr(status, "name", None)
    if isinstance(name, str):
        return name
    return str(status)


def _rss_to_mb(memory_info: object | None) -> float | None:
    """Return RSS in megabytes rounded to one decimal, or None if unknown."""
    if memory_info is None:
        return None
    rss = getattr(memory_info, "rss", None)
    if rss is None:
        return None
    return round(float(rss) / _MB, 1)


def _is_none_or_zero(value: float | None) -> bool:
    """True if ``value`` is None or numerically zero."""
    if value is None:
        return True
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return True


def _all_metrics_empty(
    cpu_percent: float | None,
    mem_rss_mb: float | None,
    mem_percent: float | None,
) -> bool:
    """True when CPU, RSS, and memory percent are all missing or zero."""
    return (
        _is_none_or_zero(cpu_percent)
        and _is_none_or_zero(mem_rss_mb)
        and _is_none_or_zero(mem_percent)
    )


def _iter_process_snapshots() -> list[dict[str, Any]]:
    """Scan all processes once and return raw metric dicts (no timestamp)."""
    rows: list[dict[str, Any]] = []
    it = iter(psutil.process_iter(attrs=_PROCESS_ATTRS))
    while True:
        try:
            proc = next(it)
        except StopIteration:
            break
        except psutil.ZombieProcess as e:
            # Iterator can raise on some OSes; still persist zombie pid/name.
            pid = getattr(e, "pid", None)
            if pid is None:
                continue
            rows.append(
                {
                    "pid": int(pid),
                    "name": getattr(e, "name", None),
                    "cmdline": None,
                    "cpu_percent": 0.0,
                    "mem_rss_mb": 0.0,
                    "mem_percent": 0.0,
                    "state": "zombie",
                    "io_read_bytes": None,
                    "io_write_bytes": None,
                }
            )
            continue
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        try:
            info = proc.info
        except psutil.ZombieProcess as e:
            # Still record zombie processes with partial info
            info = e.msg if isinstance(e.msg, dict) else {}
            pid = getattr(e, "pid", None)
            if pid is None:
                continue
            rows.append(
                {
                    "pid": int(pid),
                    "name": getattr(e, "name", None),
                    "cmdline": None,
                    "cpu_percent": 0.0,
                    "mem_rss_mb": 0.0,
                    "mem_percent": 0.0,
                    "state": "zombie",
                    "io_read_bytes": None,
                    "io_write_bytes": None,
                }
            )
            continue
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue

        pid = info.get("pid")
        if pid is None or pid == 0:
            continue

        name = info.get("name")
        cmdline = info.get("cmdline")
        if cmdline:
            cmdline_str = " ".join(str(part) for part in cmdline)
        else:
            cmdline_str = name if isinstance(name, str) and name else None

        cpu_percent = info.get("cpu_percent")
        if cpu_percent is not None:
            cpu_percent = float(cpu_percent)

        mem_rss_mb = _rss_to_mb(info.get("memory_info"))
        mem_percent = info.get("memory_percent")
        if mem_percent is not None:
            mem_percent = float(mem_percent)

        state = _status_to_state(info.get("status"))

        if _all_metrics_empty(cpu_percent, mem_rss_mb, mem_percent):
            # Zombies have no CPU/RSS; skip zero-metric filter so they are not dropped.
            if state != "zombie" and state != "Z":
                continue

        try:
            io = proc.io_counters()
            io_read_bytes = float(io.read_bytes)
            io_write_bytes = float(io.write_bytes)
        except (
            psutil.AccessDenied,
            psutil.NoSuchProcess,
            psutil.ZombieProcess,
            AttributeError,
            NotImplementedError,
        ):
            io_read_bytes = None
            io_write_bytes = None

        rows.append(
            {
                "pid": int(pid),
                "name": name if isinstance(name, str) else None,
                "cmdline": cmdline_str,
                "cpu_percent": cpu_percent,
                "mem_rss_mb": mem_rss_mb,
                "mem_percent": mem_percent,
                "state": state,
                "io_read_bytes": io_read_bytes,
                "io_write_bytes": io_write_bytes,
            }
        )
    return rows


def _sort_key_cpu(row: dict[str, Any]) -> float:
    v = row.get("cpu_percent")
    if v is None:
        return -1.0
    return float(v)


def _sort_key_mem(row: dict[str, Any]) -> float:
    v = row.get("mem_rss_mb")
    if v is None:
        return -1.0
    return float(v)


def _is_kerneliq_process(row: dict) -> bool:
    """Return True if this process belongs to KernelIQ itself.

    Matches against a fixed set of module-path substrings found in cmdline
    arguments when KernelIQ is launched via ``python -m`` or directly.  The
    ``name`` field is intentionally not matched on its own because generic
    names like ``python3`` would cause false positives; only ``cmdline`` is
    checked against KernelIQ-specific patterns.
    """
    cmdline = str(row.get("cmdline") or "").lower()
    name = str(row.get("name") or "").lower()

    kerneliq_patterns = (
        "daemon.loop",
        "kerneliq",
        "kerneliq.db",
        "shell.repl",
        "model.investigation",
        "dataset.generator",
        "dataset.logger",
        "db.retention",
    )

    for pattern in kerneliq_patterns:
        if pattern in cmdline:
            return True

    return False


def sample_top_processes(top_n: int = 10) -> list[dict[str, Any]]:
    """Return up to ``2 * top_n`` unique processes: top by CPU and top by RSS.

    Scans all running processes with ``psutil.process_iter`` and batched
    ``as_dict``-style attributes. ``cpu_percent`` is primed with one full pass,
    then a short sleep, then a second pass reads stable CPU figures (same
    pattern psutil recommends for per-process CPU).

    Processes with ``pid`` 0, or where CPU, RSS, and memory percent are all
    missing or zero, are omitted. ``NoSuchProcess``, ``AccessDenied``, and
    ``ZombieProcess`` are ignored per process.

    Args:
        top_n: How many leaders to take from the CPU ranking and how many from
            the RSS ranking before merging and deduplicating by ``pid``.

    Returns:
        A list of dicts aligned with ``process_samples`` columns:
        ``timestamp``, ``pid``, ``name``, ``cmdline``, ``cpu_percent``,
        ``mem_rss_mb``, ``mem_percent``, ``state``, ``io_read_bytes``,
        ``io_write_bytes``.
    """
    if top_n < 1:
        top_n = 1

    # Prime CPU counters so the second pass returns non-trivial cpu_percent.
    _prime_it = iter(psutil.process_iter(attrs=_PROCESS_ATTRS))
    while True:
        try:
            next(_prime_it)
        except StopIteration:
            break
        except (psutil.AccessDenied, psutil.ZombieProcess, psutil.NoSuchProcess):
            continue

    time.sleep(0.1)

    rows = _iter_process_snapshots()
    timestamp = datetime.now(timezone.utc).isoformat()

    by_cpu = sorted(rows, key=_sort_key_cpu, reverse=True)[:top_n]
    by_mem = sorted(rows, key=_sort_key_mem, reverse=True)[:top_n]

    seen: set[int] = set()
    merged: list[dict[str, Any]] = []
    for row in by_cpu:
        pid = int(row["pid"])
        if pid not in seen:
            seen.add(pid)
            merged.append(row)
    for row in by_mem:
        pid = int(row["pid"])
        if pid not in seen:
            seen.add(pid)
            merged.append(row)

    merged = [row for row in merged if not _is_kerneliq_process(row)]

    # Top-N merge can omit zombies; append every zombie from the full scan.
    for row in rows:
        if row.get("state") in ("zombie", "Z"):
            pid = int(row["pid"])
            if pid not in seen and not _is_kerneliq_process(row):
                seen.add(pid)
                merged.append(row)

    out: list[dict[str, Any]] = []
    for row in merged:
        out.append(
            {
                "timestamp": timestamp,
                "pid": row["pid"],
                "name": row["name"],
                "cmdline": row["cmdline"],
                "cpu_percent": row["cpu_percent"],
                "mem_rss_mb": row["mem_rss_mb"],
                "mem_percent": row["mem_percent"],
                "state": row["state"],
                "io_read_bytes": row.get("io_read_bytes"),
                "io_write_bytes": row.get("io_write_bytes"),
            }
        )
    return out
