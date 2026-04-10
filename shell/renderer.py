"""Format investigation results as a colored terminal diagnosis summary."""

from __future__ import annotations

_RST = "\033[0m"
_BOLD = "\033[1m"
_BOLD_CYAN = "\033[1;36m"
_BOLD_YELLOW = "\033[1;33m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"


def _confidence_color(confidence: float) -> str:
    """Return ANSI SGR for the confidence percentage (green / yellow / red)."""
    if confidence >= 70:
        return _GREEN
    if confidence >= 40:
        return _YELLOW
    return _RED


def _format_confidence_percent(confidence: float) -> str:
    """Format confidence as a percentage string for display."""
    if confidence == int(confidence):
        return str(int(confidence))
    return f"{confidence:.1f}".rstrip("0").rstrip(".")


def _observation_is_failure_style(observation: str, confidence: float) -> bool:
    """Return True if observation should be shown in red (zero confidence + failure wording)."""
    if confidence != 0:
        return False
    lower = observation.lower()
    return "failure" in lower or "error" in lower


def render_diagnosis(result: dict) -> str:
    """Render the dict returned by :func:`~model.investigation.investigate` for the terminal.

    Applies ANSI colors: bold cyan section headers, conditional confidence and
    observation coloring, and optional bold yellow command / bold proceed hint.

    Args:
        result: Investigation result with ``observation``, ``evidence``, ``action``,
            ``confidence``, and optional ``command_proposed``.

    Returns:
        A single string with section breaks and ANSI styling suitable for printing.
    """
    observation = str(result.get("observation") or "")
    evidence = str(result.get("evidence") or "")
    action = str(result.get("action") or "")
    confidence = float(result.get("confidence") or 0.0)
    raw_cmd = result.get("command_proposed")
    command_proposed = (raw_cmd or "").strip() if raw_cmd else ""

    obs_body_style = _RED if _observation_is_failure_style(observation, confidence) else ""
    pct = _format_confidence_percent(confidence)
    pct_color = _confidence_color(confidence)

    parts: list[str] = []

    parts.append(f"{_BOLD_CYAN}Observation{_RST}\n{obs_body_style}{observation}{_RST}")
    parts.append(f"{_BOLD_CYAN}Evidence{_RST}\n{evidence}")
    parts.append(f"{_BOLD_CYAN}Action{_RST}\n{action}")

    conf_line = (
        f"{_BOLD}Confidence:{_RST} {pct_color}{pct}%{_RST}"
    )
    parts.append(conf_line)

    if command_proposed:
        parts.append(
            f"{_BOLD_YELLOW}Command:{_RST} {_BOLD_YELLOW}{command_proposed}{_RST}\n"
            f"{_BOLD}Proceed? [y/N]{_RST}"
        )

    raw_full = str(result.get("raw_response") or "").strip()
    if raw_full.upper().startswith("ERROR:"):
        parts.append(f"{_BOLD_YELLOW}Backend detail{_RST}\n{_RED}{raw_full}{_RST}")

    return "\n\n".join(parts)
