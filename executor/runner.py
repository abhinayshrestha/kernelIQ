"""Run allowlisted shell commands with captured, bounded output."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from .validator import validate_system_command

_CHAIN_SPLIT_RE = re.compile(r"\s*;\s*|\s*&&\s*|\s*\|\|\s*")

# Substrings forbidden in user-confirmed commands (case-insensitive scan).
_CONFIRMED_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "sudo",
    "mkfs",
    "dd",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "chmod",
    "chown",
    "passwd",
    "useradd",
    "userdel",
    "iptables",
)

# systemctl subcommands permitted only after explicit user confirmation.
_CONFIRMED_SYSTEMCTL_SUBCOMMANDS: frozenset[str] = frozenset(
    {"start", "stop", "restart"}
)

# docker subcommands permitted only after explicit user confirmation.
_CONFIRMED_DOCKER_SUBCOMMANDS: frozenset[str] = frozenset({"stop", "restart"})

# Base executable names permitted as the first token after confirmation.
_CONFIRMED_BASE_COMMANDS: frozenset[str] = frozenset(
    {"kill", "pkill", "systemctl", "rm", "ionice", "renice", "docker"}
)

# Pipe-to-shell patterns (case-insensitive).
_PIPE_TO_SHELL_RE = re.compile(
    r"\|\s*(/usr/bin/|/bin/)?(ba)?sh\b",
    re.IGNORECASE,
)

# Block rm -rf against filesystem root only (not e.g. rm -rf /tmp).
_RM_RF_ROOT_RE = re.compile(r"\brm\s+-rf\s+/+(\s|$)", re.IGNORECASE)

_MAX_STDOUT_CHARS = 4000
_MAX_STDERR_CHARS = 1000


def _base_command_name(first_token: str) -> str:
    """Return the executable base name (last path segment) for allowlist checks."""
    return Path(first_token).name


def validate_confirmed_command(command: str) -> tuple[bool, str]:
    """Validate a shell command the user has explicitly confirmed.

    Stricter than investigation commands on chaining and metacharacters;
    allowlist is limited to post-confirmation actions only.

    Returns:
        ``(True, "")`` if valid, or ``(False, reason)`` when blocked.
    """
    text = command.strip()
    if not text:
        return False, "empty command"

    if "`" in text:
        return False, "backticks not allowed in confirmed commands"

    if "$(" in text:
        return False, "command substitution not allowed in confirmed commands"

    if re.search(r"\beval\b", text, re.IGNORECASE):
        return False, "eval not allowed in confirmed commands"

    if _PIPE_TO_SHELL_RE.search(text):
        return False, "pipe to shell not allowed in confirmed commands"

    if _CHAIN_SPLIT_RE.search(text):
        return False, "chained commands (;, &&, ||) not allowed for confirmed actions"

    lower = text.lower()
    for sub in _CONFIRMED_FORBIDDEN_SUBSTRINGS:
        if sub in lower:
            return False, f"forbidden substring in confirmed command: {sub!r}"

    if _RM_RF_ROOT_RE.search(text):
        return False, "rm -rf / is not allowed"

    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        return False, f"invalid shell quoting: {exc}"

    if not tokens:
        return False, "empty command after parsing"

    base = _base_command_name(tokens[0]).lower()
    if base not in _CONFIRMED_BASE_COMMANDS:
        return False, f"command not allowed after confirmation: {base}"

    if base == "systemctl":
        if len(tokens) < 2:
            return False, "systemctl requires a subcommand"
        if tokens[1] not in _CONFIRMED_SYSTEMCTL_SUBCOMMANDS:
            return False, (
                "systemctl subcommand not allowed after confirmation "
                f"(only start/stop/restart): {tokens[1]!r}"
            )
        return True, ""

    if base == "docker":
        if len(tokens) < 2:
            return False, "docker requires a subcommand"
        if tokens[1] not in _CONFIRMED_DOCKER_SUBCOMMANDS:
            return False, (
                "docker subcommand not allowed after confirmation "
                f"(only stop/restart): {tokens[1]!r}"
            )
        return True, ""

    return True, ""


def run_confirmed_command(command: str, timeout: int = 30) -> dict:
    """Run a shell command after explicit user confirmation.

    If :func:`~executor.validator.validate_system_command` accepts the line
    (read-only investigation allowlist), it runs without requiring the
    destructive confirmed allowlist. Otherwise :func:`validate_confirmed_command`
    applies (``kill``, ``rm``, ``systemctl`` start/stop/restart, etc.).

    Args:
        command: Full command line confirmed by the user.
        timeout: Subprocess timeout in seconds.

    Returns:
        Same shape as :func:`run_command`: on success ``success`` True,
        ``output``, ``stderr``, ``return_code``; on failure ``success`` False,
        ``error``, and ``output`` ``""``.
    """
    ok_readonly, _ = validate_system_command(command)
    if not ok_readonly:
        ok, reason = validate_confirmed_command(command)
        if not ok:
            return {"success": False, "error": reason, "output": ""}

    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "command timed out", "output": ""}
    except Exception as exc:
        return {"success": False, "error": str(exc), "output": ""}

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if len(stdout) > _MAX_STDOUT_CHARS:
        stdout = stdout[:_MAX_STDOUT_CHARS] + "\n... [output truncated]"
    if len(stderr) > _MAX_STDERR_CHARS:
        stderr = stderr[:_MAX_STDERR_CHARS] + "\n... [stderr truncated]"

    return {
        "success": True,
        "output": stdout,
        "stderr": stderr,
        "return_code": completed.returncode,
    }


def run_command(command: str, timeout: int = 30) -> dict:
    """Validate and run a shell command string, returning structured results.

    Validation uses :func:`~executor.validator.validate_system_command`.
    The command runs under ``/bin/sh``-style parsing via ``shell=True`` so
    pipelines and flags work as a single string. Stdout and stderr are
    truncated to fixed maximum lengths before being returned.

    Args:
        command: Full command line from the model or caller.
        timeout: Subprocess timeout in seconds.

    Returns:
        On success: ``success`` True, ``output`` (stdout), ``stderr``,
        and ``return_code``.
        On validation failure, timeout, or other error: ``success`` False,
        ``error`` message, and ``output`` set to ``""``.
    """
    ok, reason = validate_system_command(command)
    if not ok:
        return {"success": False, "error": reason, "output": ""}

    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "command timed out", "output": ""}
    except Exception as exc:
        return {"success": False, "error": str(exc), "output": ""}

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if len(stdout) > _MAX_STDOUT_CHARS:
        stdout = stdout[:_MAX_STDOUT_CHARS] + "\n... [output truncated]"
    if len(stderr) > _MAX_STDERR_CHARS:
        stderr = stderr[:_MAX_STDERR_CHARS] + "\n... [stderr truncated]"

    return {
        "success": True,
        "output": stdout,
        "stderr": stderr,
        "return_code": completed.returncode,
    }
