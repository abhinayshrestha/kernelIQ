"""Single-shot system telemetry snapshot for KernelIQ.

This module gathers one point-in-time view of system-wide metrics (CPU, memory,
disk, network, sensors, PSI, per-core CPU, connection counts, page faults,
buffer/cache MB) suitable for insertion into ``telemetry_samples``. It does not
schedule or loop; a daemon process is expected to call
``collect_system_snapshot()`` on an interval.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import psutil

_MB = 1048576

_SKIP_MOUNT_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/snap")
# Previous page fault counters for delta calculation between cycles
_prev_page_faults: dict[str, float] | None = None
_SKIP_FSTYPES = frozenset(
    {"tmpfs", "squashfs", "devtmpfs", "sysfs", "proc"},
)


def _bytes_to_mb(value: float | int | None) -> float | None:
    """Convert a byte count to megabytes, rounded to one decimal place."""
    if value is None:
        return None
    return round(float(value) / _MB, 1)


def _skip_partition(mountpoint: str, fstype: str) -> bool:
    """Return True if this mount should be excluded from disk usage JSON."""
    mp = mountpoint
    for prefix in _SKIP_MOUNT_PREFIXES:
        if mp == prefix or mp.startswith(prefix + "/"):
            return True
    return fstype.lower() in _SKIP_FSTYPES


def _disk_usage_payload() -> dict[str, dict[str, Any]]:
    """Build mount point -> usage stats for real filesystems only."""
    out: dict[str, dict[str, Any]] = {}
    for part in psutil.disk_partitions(all=False):
        if _skip_partition(part.mountpoint, part.fstype):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (OSError, PermissionError):
            continue
        out[part.mountpoint] = {
            "total_mb": _bytes_to_mb(usage.total),
            "used_mb": _bytes_to_mb(usage.used),
            "free_mb": _bytes_to_mb(usage.free),
            "percent": round(float(usage.percent), 1),
        }
    return out


def _disk_io_payload() -> dict[str, dict[str, int | float]] | None:
    """Build per-disk I/O counters; return None if counters are unavailable."""
    try:
        counters = psutil.disk_io_counters(perdisk=True)
    except (RuntimeError, NotImplementedError, OSError, AttributeError):
        return None
    if not counters:
        return None
    out: dict[str, dict[str, int | float]] = {}
    for name, io in counters.items():
        rb = _bytes_to_mb(io.read_bytes)
        wb = _bytes_to_mb(io.write_bytes)
        out[name] = {
            "read_count": int(io.read_count),
            "write_count": int(io.write_count),
            "read_bytes": float(rb) if rb is not None else 0.0,
            "write_bytes": float(wb) if wb is not None else 0.0,
            "read_time_ms": int(io.read_time),
            "write_time_ms": int(io.write_time),
        }
    return out


def _net_payload() -> dict[str, dict[str, float]] | None:
    """Per-interface network I/O; byte counters converted to MB in values."""
    try:
        counters = psutil.net_io_counters(pernic=True)
    except (RuntimeError, NotImplementedError, OSError, AttributeError):
        return None
    if not counters:
        return None
    out: dict[str, dict[str, float]] = {}
    for name, io in counters.items():
        out[name] = {
            "bytes_sent": _bytes_to_mb(io.bytes_sent) or 0.0,
            "bytes_recv": _bytes_to_mb(io.bytes_recv) or 0.0,
        }
    return out


def _temperature_payload() -> dict[str, float] | None:
    """Flatten psutil sensor readings to a single dict of label -> °C."""
    try:
        raw = psutil.sensors_temperatures()
    except (NotImplementedError, AttributeError, OSError, RuntimeError):
        return None
    if not raw:
        return None
    out: dict[str, float] = {}
    for sensor_name, entries in raw.items():
        for idx, entry in enumerate(entries):
            if entry.current is None:
                continue
            label = (entry.label or "").strip()
            key = f"{sensor_name}:{label}" if label else f"{sensor_name}:{idx}"
            base = key
            n = 0
            while key in out:
                n += 1
                key = f"{base}:{n}"
            out[key] = round(float(entry.current), 1)
    return out or None


def _json_or_none(obj: Any) -> str | None:
    """Serialize ``obj`` to a JSON string, or None if ``obj`` is None."""
    if obj is None:
        return None
    return json.dumps(obj, separators=(",", ":"))


def _parse_pressure_avg10(line: str) -> float | None:
    """Extract the ``avg10=`` value from one line of a PSI file."""
    for token in line.split():
        if token.startswith("avg10="):
            try:
                return float(token.split("=", 1)[1])
            except (ValueError, IndexError):
                return None
    return None


def _read_pressure_some_full(path: str) -> tuple[float | None, float | None]:
    """Read ``some`` and ``full`` avg10 from a two-line PSI file."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().strip().splitlines()
    if len(lines) < 2:
        return None, None
    return _parse_pressure_avg10(lines[0]), _parse_pressure_avg10(lines[1])


def _collect_psi() -> dict[str, float] | None:
    """Read Linux PSI avg10 from ``/proc/pressure/*`` or return None."""
    if not os.path.isdir("/proc/pressure"):
        return None
    try:
        cpu_some, cpu_full = _read_pressure_some_full("/proc/pressure/cpu")
        mem_some, mem_full = _read_pressure_some_full("/proc/pressure/memory")
        io_some, io_full = _read_pressure_some_full("/proc/pressure/io")
        if any(
            v is None
            for v in (
                cpu_some,
                cpu_full,
                mem_some,
                mem_full,
                io_some,
                io_full,
            )
        ):
            return None
        return {
            "cpu_some": float(cpu_some),
            "cpu_full": float(cpu_full),
            "memory_some": float(mem_some),
            "memory_full": float(mem_full),
            "io_some": float(io_some),
            "io_full": float(io_full),
        }
    except Exception:
        return None


def _collect_cpu_per_core() -> list[float] | None:
    """Return per-CPU utilization (one decimal) or None if unavailable."""
    try:
        percents = psutil.cpu_percent(percpu=True, interval=0.1)
        if not percents:
            return None
        return [round(float(p), 1) for p in percents]
    except Exception:
        return None


def _net_conn_status_label(conn: Any) -> str:
    """Normalize ``psutil`` connection status to an upper-case string."""
    st = conn.status
    name = getattr(st, "name", None)
    if isinstance(name, str):
        return name.upper()
    if isinstance(st, str):
        return st.upper()
    return str(st).upper()


def _collect_net_connections() -> dict[str, int] | None:
    """Count IPv4/IPv6 connections by TCP state; None on total failure."""
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return None
    except Exception:
        return None
    established = syn_recv = time_wait = close_wait = 0
    total = 0
    for c in conns:
        try:
            total += 1
            label = _net_conn_status_label(c)
            if label == "ESTABLISHED":
                established += 1
            elif label == "SYN_RECV":
                syn_recv += 1
            elif label == "TIME_WAIT":
                time_wait += 1
            elif label == "CLOSE_WAIT":
                close_wait += 1
        except Exception:
            continue
    return {
        "established": established,
        "syn_recv": syn_recv,
        "time_wait": time_wait,
        "close_wait": close_wait,
        "total": total,
    }


def _collect_page_faults() -> dict[str, float] | None:
    """Return per-interval minor and major page fault counts from /proc/vmstat.

    On the first call stores a baseline and returns zeros. Each subsequent
    call returns the delta since the previous call so the stored value
    reflects faults per collection interval rather than cumulative totals
    since boot.

    Returns:
        Dict with keys ``minor`` and ``major`` as floats, or None on error.
    """
    global _prev_page_faults
    try:
        minor: float | None = None
        major: float | None = None
        with open("/proc/vmstat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("pgfault "):
                    parts = line.split()
                    if len(parts) >= 2:
                        minor = float(parts[1])
                elif line.startswith("pgmajfault "):
                    parts = line.split()
                    if len(parts) >= 2:
                        major = float(parts[1])
        if minor is None or major is None:
            return None

        current = {"minor": minor, "major": major}

        if _prev_page_faults is None:
            # First call — store baseline, return zeros
            _prev_page_faults = current
            return {"minor": 0.0, "major": 0.0}

        # Calculate delta since last collection cycle
        delta_minor = max(0.0, minor - _prev_page_faults["minor"])
        delta_major = max(0.0, major - _prev_page_faults["major"])

        # Update stored baseline for next cycle
        _prev_page_faults = current

        return {"minor": delta_minor, "major": delta_major}

    except Exception:
        return None


def collect_system_snapshot() -> dict[str, Any]:
    """Capture one system-wide telemetry sample for ``telemetry_samples``.

    Blocks for about one second total due to ``cpu_percent`` and
    ``cpu_times_percent`` each using a 0.5s interval, plus ``cpu_percent``
    per-core sampling (0.1s). Fields that are not supported on the current OS
    (e.g. ``iowait``, load averages, temperatures, PSI) are set to ``None``
    instead of raising.

    Returns:
        A dict whose keys match ``telemetry_samples`` columns: ``timestamp``,
        ``cpu_percent``, ``load_avg_*``, ``iowait``, memory and swap MB fields,
        buffer/cache MB, PSI, per-core CPU JSON, socket counts, page faults,
        JSON string fields for disk/net/temp, and ``gpu_json`` (always ``None``
        for now).
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    cpu_percent: float | None
    try:
        cpu_percent = float(psutil.cpu_percent(interval=0.5))
    except (RuntimeError, OSError, AttributeError):
        cpu_percent = None

    iowait: float | None
    try:
        times = psutil.cpu_times_percent(interval=0.5)
        raw_iowait = getattr(times, "iowait", None)
        if raw_iowait is None:
            iowait = None
        else:
            iowait = round(float(raw_iowait), 1)
    except (RuntimeError, OSError, AttributeError, TypeError):
        iowait = None

    load_avg_1: float | None = None
    load_avg_5: float | None = None
    load_avg_15: float | None = None
    try:
        la1, la5, la15 = os.getloadavg()
        load_avg_1 = float(la1)
        load_avg_5 = float(la5)
        load_avg_15 = float(la15)
    except (OSError, AttributeError):
        pass

    mem_total_mb = mem_used_mb = mem_available_mb = None
    mem_buffers_mb = mem_cached_mb = None
    try:
        vm = psutil.virtual_memory()
        mem_total_mb = _bytes_to_mb(vm.total)
        mem_used_mb = _bytes_to_mb(vm.used)
        mem_available_mb = _bytes_to_mb(vm.available)
        mem_buffers_mb = round(vm.buffers / 1048576, 1)
        mem_cached_mb = round(vm.cached / 1048576, 1)
    except (RuntimeError, OSError, AttributeError):
        pass

    swap_total_mb = swap_used_mb = None
    try:
        sw = psutil.swap_memory()
        swap_total_mb = _bytes_to_mb(sw.total)
        swap_used_mb = _bytes_to_mb(sw.used)
    except (RuntimeError, OSError, AttributeError):
        pass

    disk_usage: dict[str, Any] | None
    try:
        disk_usage = _disk_usage_payload()
    except (RuntimeError, OSError, AttributeError):
        disk_usage = None

    disk_io: dict[str, Any] | None
    try:
        disk_io = _disk_io_payload()
    except (RuntimeError, OSError, AttributeError):
        disk_io = None

    net: dict[str, Any] | None
    try:
        net = _net_payload()
    except (RuntimeError, OSError, AttributeError):
        net = None

    temps: dict[str, float] | None
    try:
        temps = _temperature_payload()
    except (RuntimeError, OSError, AttributeError):
        temps = None

    psi = _collect_psi()
    cores = _collect_cpu_per_core()
    nc = _collect_net_connections()
    pf = _collect_page_faults()

    return {
        "timestamp": timestamp,
        "cpu_percent": cpu_percent,
        "load_avg_1": load_avg_1,
        "load_avg_5": load_avg_5,
        "load_avg_15": load_avg_15,
        "iowait": iowait,
        "mem_total_mb": mem_total_mb,
        "mem_used_mb": mem_used_mb,
        "mem_available_mb": mem_available_mb,
        "swap_total_mb": swap_total_mb,
        "swap_used_mb": swap_used_mb,
        "disk_usage_json": _json_or_none(disk_usage),
        "disk_io_json": _json_or_none(disk_io),
        "net_json": _json_or_none(net),
        "temp_json": _json_or_none(temps),
        "gpu_json": None,
        "psi_cpu_some": psi["cpu_some"] if psi else None,
        "psi_cpu_full": psi["cpu_full"] if psi else None,
        "psi_memory_some": psi["memory_some"] if psi else None,
        "psi_memory_full": psi["memory_full"] if psi else None,
        "psi_io_some": psi["io_some"] if psi else None,
        "psi_io_full": psi["io_full"] if psi else None,
        "cpu_per_core_json": json.dumps(cores) if cores else None,
        "mem_buffers_mb": mem_buffers_mb,
        "mem_cached_mb": mem_cached_mb,
        "net_connections_established": nc["established"] if nc else None,
        "net_connections_syn_recv": nc["syn_recv"] if nc else None,
        "net_connections_time_wait": nc["time_wait"] if nc else None,
        "net_connections_close_wait": nc["close_wait"] if nc else None,
        "net_connections_total": nc["total"] if nc else None,
        "page_faults_minor": pf["minor"] if pf else None,
        "page_faults_major": pf["major"] if pf else None,
    }
