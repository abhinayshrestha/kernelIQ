"""Deterministic condensation of executor output into compact
diagnostic digests.

Tool-aware rules extract signal from known command output
structures. Four specialist condensers handle the high-frequency
commands. Everything else uses a generic truncator.
No LLM calls — instant, free, deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path

_GENERIC_MAX_LINES = 8
_GENERIC_MAX_CHARS = 600
_JOURNALCTL_MAX_KEPT_LINES = 20
_PS_MAX_PROCESS_LINES = 5

_JOURNALCTL_KEYWORDS = re.compile(
    r"error|fail|fatal|critical|denied|refused|killed|"
    r"segfault|oom|timeout|address in use|no space left",
    re.IGNORECASE,
)

_LOOP_DEVICE_RE = re.compile(r"^loop\d+$")

_UUID_RE = re.compile(r"[0-9a-f]{8,}", re.IGNORECASE)


def condense_command(command: str, raw_output: str) -> str:
    """Condense raw shell command output using tool-aware rules.

    Routes to a specialist condenser based on the base command
    name. Falls back to generic truncation for unknown tools.

    Args:
        command: The full shell command string that was run.
        raw_output: Raw stdout/stderr string from the executor.

    Returns:
        Compact digest string ready for the context window.
    """
    if not raw_output or not raw_output.strip():
        return "- (no output)"

    base = _get_base_command(command)

    if base == "ps":
        return _condense_ps(raw_output)
    elif base == "iostat":
        return _condense_iostat(raw_output)
    elif base == "journalctl":
        return _condense_journalctl(raw_output)
    elif base == "systemctl":
        return _condense_systemctl(raw_output, command)
    else:
        return _generic_truncate(raw_output)


def condense_sql(sql: str, formatted_rows: str) -> str:
    """Pass formatted SQL result through unchanged.

    SQL output is already clean tabular data after _format_sql_result.
    No further condensation needed.

    Args:
        sql: The SQL query string that was run.
        formatted_rows: Already-formatted table string from
            _format_sql_result in sql_runner.py.

    Returns:
        formatted_rows unchanged.
    """
    return formatted_rows


def _get_base_command(command: str) -> str:
    """Extract the base executable name from a full command string.

    Handles pipes, flags, and full paths.
    Example: "/usr/bin/ps aux --sort=-%cpu | head -10" -> "ps"

    Args:
        command: Full command string.

    Returns:
        Lowercase base executable name.
    """
    first_token = command.strip().split()[0] if command.strip() else ""
    return Path(first_token).name.lower()


def _condense_ps(raw: str) -> str:
    """Keep header line plus top 5 process lines.

    Truncates cmdline to executable name plus first meaningful
    argument. Drops UUID-like path segments.

    Args:
        raw: Raw ps output string.

    Returns:
        Condensed string with header and top processes.
    """
    lines = raw.strip().splitlines()
    if not lines:
        return "- (no output)"

    header = lines[0]
    process_lines = lines[1:_PS_MAX_PROCESS_LINES + 1]
    total = len(lines) - 1

    cleaned = []
    for line in process_lines:
        cleaned.append(_truncate_ps_cmdline(line))

    result_lines = [header] + cleaned
    if total > _PS_MAX_PROCESS_LINES:
        result_lines.append(
            f"... ({total - _PS_MAX_PROCESS_LINES} more processes)"
        )
    return "\n".join(result_lines)


def _truncate_ps_cmdline(line: str) -> str:
    """Truncate long path segments and UUIDs in a ps output line.

    Args:
        line: One line of ps output.

    Returns:
        Line with long paths reduced to basename.
    """
    tokens = line.split()
    result = []
    for token in tokens:
        if "/" in token and len(token) > 30:
            base = Path(token).name
            if _UUID_RE.search(base):
                base = base[:8] + "..."
            result.append(base)
        else:
            result.append(token)
    return " ".join(result)


def _condense_iostat(raw: str) -> str:
    """Skip CPU block, keep device table minus loop devices.

    Retains only Device, r_await, w_await, wkB/s, rkB/s columns
    from the device rows.

    Args:
        raw: Raw iostat output string.

    Returns:
        Condensed device table string.
    """
    lines = raw.strip().splitlines()
    device_section = False
    header_line = ""
    device_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Device"):
            device_section = True
            header_line = line
            continue
        if device_section and stripped:
            device_name = stripped.split()[0]
            if not _LOOP_DEVICE_RE.match(device_name):
                device_lines.append(line)

    if not device_lines:
        return _generic_truncate(raw)

    result = [header_line] + device_lines
    return "\n".join(result)


def _condense_journalctl(raw: str) -> str:
    """Keep only lines matching diagnostic keywords.

    Falls back to last 10 lines if no keyword matches found.
    Always appends a summary of lines scanned vs kept.

    Args:
        raw: Raw journalctl output string.

    Returns:
        Condensed string with diagnostic lines only.
    """
    lines = raw.strip().splitlines()
    if not lines:
        return "- (no output)"

    matched = [
        line for line in lines
        if _JOURNALCTL_KEYWORDS.search(line)
    ]

    total = len(lines)

    if not matched:
        kept = lines[-10:]
        result = "\n".join(kept)
        result += f"\n(no keyword matches — showing last 10 of {total} lines)"
        return result

    kept = matched[:_JOURNALCTL_MAX_KEPT_LINES]
    result = "\n".join(kept)
    if len(matched) > _JOURNALCTL_MAX_KEPT_LINES or total > len(matched):
        result += (
            f"\n({len(kept)} matching lines shown, "
            f"{total - len(kept)} lines dropped)"
        )
    return result


def _condense_systemctl(raw: str, command: str) -> str:
    """Branch on systemctl subcommand.

    list-units: drop inactive/running rows, keep only
    non-normal states. If all normal, return summary line.
    status/show/is-active/is-failed: pass through with
    generic truncation.

    Args:
        raw: Raw systemctl output string.
        command: Full command string to detect subcommand.

    Returns:
        Condensed systemctl output.
    """
    if "list-units" in command:
        return _condense_systemctl_list_units(raw)
    return _generic_truncate(raw)


def _condense_systemctl_list_units(raw: str) -> str:
    """Keep only non-healthy unit rows from list-units output.

    Drops header, footer legend, and any row that is
    active/running or inactive/dead. Returns a summary
    line if everything is healthy.

    Args:
        raw: Raw systemctl list-units output.

    Returns:
        Filtered unit rows or "all units healthy" message.
    """
    lines = raw.strip().splitlines()
    problem_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("UNIT") or stripped.startswith("●"):
            continue
        if "legend" in stripped.lower() or stripped.startswith("To show"):
            continue
        lower = stripped.lower()
        if "failed" in lower or "activating" in lower or \
           "deactivating" in lower or "reloading" in lower:
            problem_lines.append(line)

    if not problem_lines:
        total = sum(
            1 for l in lines
            if l.strip() and not l.strip().startswith("UNIT")
            and "legend" not in l.lower()
        )
        return f"(all {total} units in normal state — no failures detected)"

    return "\n".join(problem_lines)


def _generic_truncate(raw: str) -> str:
    """Keep first N lines capped at M characters.

    Used for all commands without a specialist condenser.

    Args:
        raw: Raw output string.

    Returns:
        Truncated string with line count note if truncated.
    """
    lines = raw.strip().splitlines()
    if not lines:
        return "- (no output)"

    total = len(lines)
    kept = lines[:_GENERIC_MAX_LINES]
    result = "\n".join(kept)

    if len(result) > _GENERIC_MAX_CHARS:
        result = result[:_GENERIC_MAX_CHARS]

    if total > _GENERIC_MAX_LINES:
        result += f"\n... ({total - _GENERIC_MAX_LINES} more lines)"

    return result
