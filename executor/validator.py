"""Deterministic validation for model-requested shell commands and SQL.

Rules are intentionally conservative: unclear or unusual constructs are
rejected. This module enforces the policy defined in ``whitelist``; it does
not run commands or queries.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .whitelist import (
    ALLOWED_COMMANDS,
    ALLOWED_DOCKER_SUBCOMMANDS,
    ALLOWED_SYSTEMCTL_SUBCOMMANDS,
    BLOCKED_PATTERNS,
)

_CHAIN_SPLIT_RE = re.compile(r"\s*;\s*|\s*&&\s*")

# Forbidden SQL keywords as whole tokens (avoids matching e.g. "UPDATED" for UPDATE).
_FORBIDDEN_SQL_TOKENS_RE = re.compile(
    r"\b(DELETE|DROP|ALTER|TRUNCATE|UPDATE)\b",
    re.IGNORECASE,
)


def _base_command_name(first_token: str) -> str:
    """Return the executable base name (last path segment) for allowlist checks."""
    return Path(first_token).name


def _validate_docker_subcommand(tokens: list[str]) -> tuple[bool, str]:
    """Check docker subcommand against ``ALLOWED_DOCKER_SUBCOMMANDS``."""
    if len(tokens) < 2:
        return False, "docker requires a subcommand"
    sub = tokens[1:]
    if len(sub) >= 2:
        two = f"{sub[0]} {sub[1]}"
        if two in ALLOWED_DOCKER_SUBCOMMANDS:
            return True, ""
    if sub[0] in ALLOWED_DOCKER_SUBCOMMANDS:
        return True, ""
    return False, f"docker subcommand not allowed: {sub[0]}"


def _validate_single_segment(segment: str) -> tuple[bool, str]:
    """Apply allowlist rules to one shell segment (no ``;`` / ``&&`` inside)."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError as exc:
        return False, f"invalid shell quoting: {exc}"

    if not tokens:
        return False, "empty command after parsing"

    raw_base = tokens[0]
    base = _base_command_name(raw_base).lower()

    if base not in ALLOWED_COMMANDS:
        return False, f"command not allowed: {base}"

    if base == "systemctl":
        if len(tokens) < 2:
            return False, "systemctl requires a subcommand"
        sub = tokens[1]
        if sub not in ALLOWED_SYSTEMCTL_SUBCOMMANDS:
            return False, f"systemctl subcommand not allowed: {sub}"
        return True, ""

    if base == "docker":
        return _validate_docker_subcommand(tokens)

    return True, ""


def validate_system_command(command: str) -> tuple[bool, str]:
    """Validate a raw shell command string the model may run.

    Returns ``(True, "")`` if the command passes all checks, or
    ``(False, reason)`` with a short explanation when blocked.

    Chained commands (``;`` or ``&&``) require every segment to pass the same
    base-command and subcommand rules as a standalone invocation.
    """
    text = command.strip()
    if not text:
        return False, "empty command"

    for pattern in BLOCKED_PATTERNS:
        if pattern in text:
            return False, f"forbidden substring in command: {pattern!r}"

    if _CHAIN_SPLIT_RE.search(text):
        for part in _CHAIN_SPLIT_RE.split(text):
            seg = part.strip()
            if not seg:
                return False, "empty command segment in chain"
            ok, reason = _validate_single_segment(seg)
            if not ok:
                return False, reason
        return True, ""

    return _validate_single_segment(text)


def validate_sql(sql: str) -> tuple[bool, str]:
    """Validate a raw SQL string the model may run.

    Only a single statement starting with ``SELECT`` or ``INSERT`` is allowed.
    Dangerous keywords and multi-statement injection are rejected.
    """
    text = sql.strip()
    if not text:
        return False, "empty SQL"

    head = text.lstrip()
    u = head.upper()
    if not (u.startswith("SELECT") or u.startswith("INSERT")):
        return False, "SQL must start with SELECT or INSERT"

    if _FORBIDDEN_SQL_TOKENS_RE.search(text):
        return False, "forbidden SQL keyword (DELETE/DROP/ALTER/TRUNCATE/UPDATE)"

    semicolon = text.find(";")
    if semicolon != -1:
        tail = text[semicolon + 1 :].strip()
        if tail:
            return False, "multiple SQL statements or trailing SQL after ';'"

    return True, ""
