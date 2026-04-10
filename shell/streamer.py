"""Live status lines for investigation steps (stderr, dim ANSI colors)."""

from __future__ import annotations

import sys

_DIM_CYAN = "\033[2;36m"
_DIM_BLUE = "\033[2;34m"
_DIM_GRAY = "\033[2;90m"
_RST = "\033[0m"

_MAX_DETAIL_LEN = 80


def print_step(step_type: str, detail: str) -> None:
    """Print a one-line investigation status to stderr for live feedback.

    Used as ``on_step`` for :func:`~model.investigation.investigate``. For
    ``command`` and unknown step types, detail is truncated to 80 characters.
    Colors are dim cyan (command), dim blue (sql), or dim gray (thinking).
    SQL steps show a friendly message derived from table names in ``detail``,
    never the raw query.

    Args:
        step_type: One of ``\"command\"``, ``\"sql\"``, or ``\"thinking\"``.
        detail: Command line, SQL text (matched for keywords only), or thinking summary.
    """
    if step_type == "command":
        d = detail if len(detail) <= _MAX_DETAIL_LEN else detail[: _MAX_DETAIL_LEN - 3] + "..."
        line = f"{_DIM_CYAN}  Running: {d}{_RST}"
    elif step_type == "sql":
        if "process_samples" in detail:
            msg = "  Querying process history..."
        elif "telemetry_samples" in detail:
            msg = "  Querying system telemetry..."
        elif "service_samples" in detail:
            msg = "  Querying service history..."
        elif "investigation_logs" in detail:
            msg = "  Querying past investigations..."
        elif "diagnoses" in detail:
            msg = "  Querying diagnosis history..."
        elif "alerts" in detail:
            msg = "  Checking alerts..."
        else:
            msg = "  Querying database..."
        line = f"{_DIM_BLUE}{msg}{_RST}"
    elif step_type == "thinking":
        line = f"{_DIM_GRAY}  Analyzing...{_RST}"
    else:
        d = detail if len(detail) <= _MAX_DETAIL_LEN else detail[: _MAX_DETAIL_LEN - 3] + "..."
        line = f"{_DIM_GRAY}  {step_type}: {d}{_RST}"

    print(line, file=sys.stderr, flush=True)
