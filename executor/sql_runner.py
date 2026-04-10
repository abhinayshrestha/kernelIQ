"""Run validated SQL (``SELECT`` / ``INSERT``) against the telemetry database."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from db.helpers import get_connection, query

from .validator import validate_sql

_MAX_SELECT_ROWS = 20

# ---------------------------------------------------------------------------
# _format_sql_result
# ---------------------------------------------------------------------------

TELEMETRY_ONLY_COLUMNS = {
    "cpu_percent", "load_avg_1", "load_avg_5", "load_avg_15",
    "iowait", "mem_total_mb", "mem_used_mb", "mem_available_mb",
    "mem_buffers_mb", "mem_cached_mb", "swap_total_mb", "swap_used_mb",
    "disk_io_json", "disk_usage_json", "net_json", "temp_json", "gpu_json",
    "psi_cpu_some", "psi_cpu_full", "psi_memory_some", "psi_memory_full",
    "psi_io_some", "psi_io_full", "cpu_per_core_json",
    "page_faults_minor", "page_faults_major",
    "net_connections_established", "net_connections_syn_recv",
    "net_connections_time_wait", "net_connections_close_wait",
    "net_connections_total",
}

PROCESS_ONLY_COLUMNS = {
    "pid", "name", "cmdline", "cpu_percent", "mem_rss_mb",
    "mem_percent", "state", "io_read_bytes", "io_write_bytes",
}

SERVICE_ONLY_COLUMNS = {
    "service_name", "active_state", "sub_state",
}


def _detect_table(keys: list[str]) -> str:
    """Return 'telemetry', 'process', 'service', or 'generic'."""
    key_set = set(keys)
    if key_set & TELEMETRY_ONLY_COLUMNS:
        return "telemetry"
    if key_set & PROCESS_ONLY_COLUMNS:
        return "process"
    if key_set & SERVICE_ONLY_COLUMNS:
        return "service"
    return "generic"


def _fmt_timestamp(value: Any) -> str:
    """Truncate ISO timestamp to seconds, drop microseconds and timezone."""
    s = str(value)
    # Match the datetime portion up to seconds
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", s)
    return m.group(1) if m else s


def _fmt_float(value: Any) -> Any:
    """Round floats to 2 decimal places; leave other types alone."""
    if isinstance(value, float):
        return round(value, 2)
    return value


def _apply_universal(key: str, value: Any) -> Any:
    """Apply transforms that apply to every table."""
    if value is None:
        return "null"
    if key == "timestamp" or key.endswith("timestamp"):
        return _fmt_timestamp(value)
    return _fmt_float(value)


# --- telemetry helpers ---

def _fmt_disk_io(raw: Any) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        parts = []
        for dev, stats in data.items():
            rb = stats.get("read_bytes", 0.0)
            wb = stats.get("write_bytes", 0.0)
            rt = stats.get("read_time_ms", 0)
            wt = stats.get("write_time_ms", 0)
            if rb == 0.0 and wb == 0.0 and rt == 0 and wt == 0:
                continue
            parts.append(
                f"{dev}: write={wb}MB {wt}ms read={rb}MB {rt}ms"
            )
        return " | ".join(parts) if parts else "disk_io: no activity"
    except Exception:
        return "[unparseable]"


def _fmt_disk_usage(raw: Any) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        parts = []
        for mount, stats in data.items():
            pct = stats.get("percent", 0.0)
            used = stats.get("used_mb", 0.0)
            free = stats.get("free_mb", 0.0)
            parts.append(f"{mount} {pct}% (used {used}MB free {free}MB)")
        return " | ".join(parts)
    except Exception:
        return "[unparseable]"


def _fmt_cpu_per_core(raw: Any) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        cores = [round(float(v), 2) for v in data]
        return f"cores: {cores}"
    except Exception:
        return "[unparseable]"


def _fmt_net_json(raw: Any) -> str:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        parts = []
        for iface, stats in data.items():
            if iface == "lo":
                continue
            sent = stats.get("bytes_sent", 0.0)
            recv = stats.get("bytes_recv", 0.0)
            parts.append(f"{iface}: sent={sent}MB recv={recv}MB")
        return " | ".join(parts) if parts else "net: no interfaces"
    except Exception:
        return "[unparseable]"


def _fmt_temp_json(raw: Any) -> str:
    if raw is None:
        return "null"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        parts = [f"{sensor}: {round(float(temp), 2)}\u00b0C" for sensor, temp in data.items()]
        return " | ".join(parts)
    except Exception:
        return "[unparseable]"


def _transform_telemetry_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        v = _apply_universal(k, v)
        if k == "disk_io_json" and v != "null":
            v = _fmt_disk_io(v)
        elif k == "disk_usage_json" and v != "null":
            v = _fmt_disk_usage(v)
        elif k == "cpu_per_core_json" and v != "null":
            v = _fmt_cpu_per_core(v)
        elif k == "net_json" and v != "null":
            v = _fmt_net_json(v)
        elif k == "temp_json":
            v = _fmt_temp_json(row[k])
        elif k == "gpu_json":
            v = "no GPU data" if (row[k] is None) else row[k]
        out[k] = v
    return out


# --- process helpers ---

_UUID_RE = re.compile(r"[0-9a-f]{8,}", re.IGNORECASE)


def _fmt_cmdline(raw: Any) -> str:
    if raw is None:
        return "null"
    s = str(raw)
    tokens = s.split()
    if not tokens:
        return s
    exe = tokens[0].split("/")[-1]
    if len(tokens) > 1:
        arg = tokens[1]
        if not arg.startswith("--") and not _UUID_RE.search(arg):
            return f"{exe} {arg}"
    return exe


def _transform_process_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        v = _apply_universal(k, v)
        if k == "mem_percent" and v != "null":
            v = f"{v}%"
        elif k == "cmdline":
            v = _fmt_cmdline(row[k])
        elif k in ("io_write_bytes", "io_read_bytes") and v != "null":
            v = f"{v} (cumulative)"
        out[k] = v
    return out


# --- service helpers ---

def _transform_service_rows(rows: list[dict]) -> tuple[list[dict], str | None]:
    """Apply service-specific filtering; return (transformed_rows, note)."""
    transformed = [{k: _apply_universal(k, v) for k, v in r.items()} for r in rows]
    total = len(transformed)
    note = None

    if total > 5:
        failed = [r for r in transformed if r.get("active_state") == "failed" or r.get("sub_state") == "failed"]
        normal = [r for r in transformed if r not in failed]
        all_normal = all(
            r.get("active_state") in ("inactive", "active", None) and
            r.get("active_state") != "failed"
            for r in transformed
        )
        if all_normal:
            transformed = transformed[:5]
            note = f"(showing 5 of {total} rows — all in normal state)"
        elif failed:
            kept_normal = [r for r in normal if r not in failed][:max(0, 5 - len(failed))]
            transformed = failed + kept_normal

    return transformed, note


# --- generic ---

def _transform_generic_row(row: dict) -> dict:
    return {k: _apply_universal(k, v) for k, v in row.items()}


# --- rendering ---

def _render_table(rows: list[dict], summary: str) -> str:
    if not rows:
        return summary
    keys = list(rows[0].keys())
    col_widths = [len(k) for k in keys]
    str_rows: list[list[str]] = []
    for row in rows:
        str_row = [str(row.get(k, "null")) for k in keys]
        for i, cell in enumerate(str_row):
            col_widths[i] = max(col_widths[i], len(cell))
        str_rows.append(str_row)

    def pad(s: str, w: int) -> str:
        return s.ljust(w)

    sep = "  "
    header = sep.join(pad(k, col_widths[i]) for i, k in enumerate(keys))
    data_lines = [
        sep.join(pad(cell, col_widths[i]) for i, cell in enumerate(str_row))
        for str_row in str_rows
    ]
    lines = [header] + data_lines + [summary]
    return "\n".join(lines)


def _format_sql_result(result: dict) -> str:
    """Format a ``run_query`` result dict as a human-readable string.

    Detects the source table from column names and applies table-specific
    transforms before rendering a column-aligned table.  Error, empty, and
    INSERT result paths are returned unchanged.
    """
    if not result.get("success", False):
        return f"SQL error: {result.get('error', 'unknown error')}"

    if "rows_affected" in result:
        return f"rows_affected={result['rows_affected']}"

    rows: list[dict] = result.get("rows", [])
    if not rows:
        return "SQL returned 0 rows."

    keys = list(rows[0].keys())
    table_type = _detect_table(keys)
    extra_note: str | None = None

    if table_type == "telemetry":
        transformed = [_transform_telemetry_row(r) for r in rows]
    elif table_type == "process":
        transformed = [_transform_process_row(r) for r in rows]
        if len(transformed) > 5:
            _io_zero = {"null", "0.0 (cumulative)"}

            def _is_inactive(r: dict) -> bool:
                write_ok = r.get("io_write_bytes", "null") in _io_zero
                read_ok = "io_read_bytes" not in r or r.get("io_read_bytes", "null") in _io_zero
                cpu_ok = "cpu_percent" not in r or str(r.get("cpu_percent", "null")) in {"null", "0.0"}
                return write_ok and read_ok and cpu_ok

            active = [r for r in transformed if not _is_inactive(r)]
            if active:
                hidden = len(transformed) - len(active)
                if hidden > 0:
                    transformed = active
                    extra_note = f"({hidden} rows with no activity hidden)"
    elif table_type == "service":
        transformed, extra_note = _transform_service_rows(rows)
    else:
        transformed = [_transform_generic_row(r) for r in rows]

    shown = len(transformed)
    total_matched = result.get("row_count", shown)
    truncated = result.get("truncated", False)

    if truncated:
        summary = f"({shown} rows shown, {result.get('note', '')})"
    else:
        summary = f"({shown} rows)"

    rendered = _render_table(transformed, summary)
    if extra_note:
        rendered = rendered + "\n" + extra_note
    return rendered


def run_query(db_path: str, sql: str, params: tuple = ()) -> dict:
    """Validate and execute a single ``SELECT`` or ``INSERT`` on ``db_path``.

    Only statements that pass :func:`~executor.validator.validate_sql` are
    executed. ``SELECT`` rows are limited to :data:`_MAX_SELECT_ROWS`; when
    more rows exist in the full result set, ``truncated`` is ``True``.

    Args:
        db_path: Path to the SQLite database file.
        sql: One SQL statement (``SELECT`` or ``INSERT``).
        params: Values bound to ``?`` placeholders.

    Returns:
        For ``SELECT``: ``success``, ``rows`` (list of dicts), ``row_count``,
        ``truncated``, and when truncated a ``note`` explaining the cap.
        For ``INSERT``: ``success`` and ``rows_affected``.
        On validation or SQLite errors: ``success`` False, ``error``, and
        ``rows`` as an empty list.
    """
    ok, reason = validate_sql(sql)
    if not ok:
        return {"success": False, "error": reason, "rows": []}

    head = sql.strip().lstrip().upper()
    if head.startswith("SELECT"):
        try:
            all_rows = query(db_path, sql, params)
        except sqlite3.Error as exc:
            return {"success": False, "error": str(exc), "rows": []}

        truncated = len(all_rows) > _MAX_SELECT_ROWS
        results = all_rows[:_MAX_SELECT_ROWS] if truncated else all_rows
        out: dict = {
            "success": True,
            "rows": results,
            "row_count": len(results),
            "truncated": truncated,
        }
        if truncated:
            out["note"] = (
                f"Results truncated to {_MAX_SELECT_ROWS} rows "
                f"({len(all_rows)} rows matched)."
            )
        return out

    if head.startswith("INSERT"):
        conn = get_connection(db_path)
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return {"success": True, "rows_affected": cur.rowcount}
        except sqlite3.Error as exc:
            return {"success": False, "error": str(exc), "rows": []}
        finally:
            conn.close()

    return {"success": False, "error": "SQL must start with SELECT or INSERT", "rows": []}
