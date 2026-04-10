"""KernelIQ REPL startup banner with live telemetry, daemon, and Ollama status.

Renders a framed header, resource bars from the latest SQLite telemetry row,
systemd/process daemon state, LLM backend reachability, optional alert count,
and dataset collection stats. Intended to run once when the interactive shell
starts.
"""

from __future__ import annotations

import json
import subprocess

from db.helpers import query
from db.schema import get_db_path
from model.client import is_ollama_available
from dataset.logger import get_dataset_stats
from config import (
    HTTP_LLM_API_KEY,
    HTTP_LLM_MODEL,
    MODEL_BACKEND,
    OLLAMA_BASE_URL,
)

BOLD_CYAN = "\033[1;36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RST = "\033[0m"


def _make_bar(percent: float, width: int = 10) -> str:
    """Return a Unicode block progress bar of ``width`` cells.

    Filled cells use ``█``, empty cells ``░``. ``percent`` is clamped to
    ``[0, 100]`` before computing how many cells are filled.
    """
    p = max(0.0, min(100.0, float(percent)))
    filled = round(p / 100.0 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _color_percent(percent: float, bar: str) -> str:
    """Wrap ``bar`` in ANSI foreground color from ``percent`` thresholds.

    Below 60%: green; below 80%: yellow; otherwise red. Resets after the bar.
    """
    p = float(percent)
    if p < 60.0:
        code = "\033[32m"
    elif p < 80.0:
        code = "\033[33m"
    else:
        code = "\033[31m"
    return f"{code}{bar}{RST}"


def _get_latest_telemetry(db_path: str) -> dict | None:
    """Return the newest ``telemetry_samples`` row as a flat dict, or ``None``.

    Keys: ``cpu_percent``, ``load_avg_1``, ``load_avg_5``, ``load_avg_15``,
    ``iowait``, ``mem_used_mb``, ``mem_total_mb``, ``mem_available_mb``,
    ``swap_used_mb``, ``disk_usage_json``.
    """
    try:
        rows = query(
            db_path,
            (
                "SELECT cpu_percent, load_avg_1, load_avg_5, load_avg_15, iowait, "
                "mem_used_mb, mem_total_mb, mem_available_mb, swap_used_mb, "
                "disk_usage_json FROM telemetry_samples "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            (),
        )
    except Exception:
        return None
    if not rows:
        return None
    return rows[0]


def _get_disk_percent(disk_usage_json: str) -> float:
    """Parse ``disk_usage_json`` and return root ``/`` usage percent, or ``0.0``."""
    if not disk_usage_json or not str(disk_usage_json).strip():
        return 0.0
    try:
        data = json.loads(disk_usage_json)
    except (json.JSONDecodeError, TypeError):
        return 0.0
    if not isinstance(data, dict):
        return 0.0
    entry = data.get("/")
    if not isinstance(entry, dict):
        return 0.0
    pct = entry.get("percent")
    try:
        return float(pct)
    except (TypeError, ValueError):
        return 0.0


def _check_daemon_running() -> bool:
    """True if user ``kerneliq-daemon`` is active, or a ``daemon.loop`` process exists."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "kerneliq-daemon"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.stdout.strip() == "active":
            return True
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        r2 = subprocess.run(
            ["sh", "-c", 'ps aux | grep "daemon.loop" | grep -v grep'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return bool(r2.stdout.strip())


def _get_unresolved_alerts(db_path: str) -> int:
    """Count alerts with ``resolved = 0``; return ``0`` if the query fails."""
    try:
        rows = query(
            db_path,
            "SELECT COUNT(*) AS cnt FROM alerts WHERE resolved = 0",
            (),
        )
        if not rows:
            return 0
        cnt = rows[0].get("cnt")
        return int(cnt) if cnt is not None else 0
    except Exception:
        return 0


def _pct_label(n: float) -> str:
    """Format a percentage for display in four columns including ``%``."""
    return f"{round(float(n)):>3}%"


def _f_load(x: object) -> float:
    try:
        return float(x) if x is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def render_banner() -> None:
    """Print the KernelIQ startup banner to stdout (telemetry, daemon, Ollama, dataset)."""
    db_path = get_db_path()
    tel = _get_latest_telemetry(db_path)
    alerts_n = _get_unresolved_alerts(db_path)
    daemon_ok = _check_daemon_running()
    stats = get_dataset_stats()
    total_examples = int(stats.get("total_examples", 0))

    print(f"{BOLD_CYAN}╔════════════════════════════════════════════════════════╗{RST}")
    print(f"{BOLD_CYAN}║              KernelIQ  v0.1.0                          ║{RST}")
    print(f"{BOLD_CYAN}║         Local System Diagnosis Agent                   ║{RST}")
    print(f"{BOLD_CYAN}╚════════════════════════════════════════════════════════╝{RST}")
    print()

    if tel is None:
        print(f"{DIM}  No telemetry data yet — daemon may still be starting up.{RST}")
        print()
    else:
        cpu_p = _f_load(tel.get("cpu_percent"))
        ram_total = tel.get("mem_total_mb")
        ram_used = tel.get("mem_used_mb")
        if ram_total is not None and ram_used is not None:
            try:
                rt = float(ram_total)
                ru = float(ram_used)
                ram_p = (ru / rt * 100.0) if rt > 0 else 0.0
            except (TypeError, ValueError):
                ram_p = 0.0
        else:
            ram_p = 0.0
        disk_p = _get_disk_percent(str(tel.get("disk_usage_json") or ""))

        cpu_bar = _color_percent(cpu_p, _make_bar(cpu_p))
        ram_bar = _color_percent(ram_p, _make_bar(ram_p))
        disk_bar = _color_percent(disk_p, _make_bar(disk_p))

        la1 = _f_load(tel.get("load_avg_1"))
        la5 = _f_load(tel.get("load_avg_5"))
        la15 = _f_load(tel.get("load_avg_15"))
        load_s = f"{la1:.2f} / {la5:.2f} / {la15:.2f}"

        swap_used = tel.get("swap_used_mb")
        try:
            swap_i = int(float(swap_used)) if swap_used is not None else 0
        except (TypeError, ValueError):
            swap_i = 0

        io_w = _f_load(tel.get("iowait"))

        print(f"  CPU    {cpu_bar}  {_pct_label(cpu_p)}     Load   {load_s}")
        print()
        print(f"  RAM    {ram_bar}  {_pct_label(ram_p)}     Swap   {swap_i} MB used")
        print()
        print(f"  Disk   {disk_bar}  {_pct_label(disk_p)}     IO     {io_w:.1f}% wait")
        print()

    if daemon_ok:
        print(f"{GREEN}  ● Daemon    active")
    else:
        print(
            f"{RED}  ✗ Daemon    not running — start with: python3 -m daemon.loop{RST}"
        )

    _CLOUD_BACKENDS = {"deepseek", "openai", "claude", "google"}
    if MODEL_BACKEND in _CLOUD_BACKENDS:
        model_label = HTTP_LLM_MODEL or MODEL_BACKEND
        if HTTP_LLM_API_KEY:
            print(
                f"{GREEN}  ● Backend    active — {MODEL_BACKEND} ({model_label}){RST}"
            )
        else:
            print(
                f"{RED}  ✗ Backend    {MODEL_BACKEND} — API key not set in kerneliq.toml{RST}"
            )
    else:
        ollama_ok = is_ollama_available()
        if ollama_ok:
            print(
                f"{GREEN}  ● Backend    active — Ollama at {OLLAMA_BASE_URL}{RST}"
            )
        else:
            print(
                f"{RED}  ✗ Ollama     not reachable — diagnoses will not work{RST}"
            )

    if alerts_n > 0:
        print(
            f"{YELLOW}  ⚠ {alerts_n} unresolved alert(s) — type !alerts to view{RST}"
        )

    print()
    print(f"{DIM}  Type your question or !help for commands.{RST}")
    print()
