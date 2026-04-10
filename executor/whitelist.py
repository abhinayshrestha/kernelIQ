"""Security boundary: allowed read-only commands and forbidden patterns.

KernelIQ runs model-requested shell commands and SQL only after deterministic
checks. This module defines what may appear in a command string and which
subcommands are permitted for multi-word tools. It does not execute anything;
``validator`` applies these rules. When extending the allowlists, prefer
narrow, read-only operations and avoid shell metacharacters that change
interpretation.
"""

from __future__ import annotations

ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "ps",
        "top",
        "htop",
        "pgrep",
        "pidof",
        "pstree",
        "free",
        "vmstat",
        "slabtop",
        "df",
        "du",
        "lsblk",
        "findmnt",
        "iostat",
        "stat",
        "file",
        "ss",
        "netstat",
        "lsof",
        "ip",
        "nslookup",
        "dig",
        "ping",
        "traceroute",
        "ls",
        "cat",
        "head",
        "tail",
        "find",
        "wc",
        "grep",
        "awk",
        "sort",
        "uniq",
        "journalctl",
        "dmesg",
        "last",
        "lastb",
        "who",
        "w",
        "systemctl",
        "uptime",
        "uname",
        "hostname",
        "timedatectl",
        "lscpu",
        "lspci",
        "lsusb",
        "nvidia-smi",
        "rocm-smi",
        "docker",
    }
)

ALLOWED_SYSTEMCTL_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "list-units",
        "is-active",
        "is-failed",
        "show",
        "list-timers",
        "list-sockets",
    }
)

ALLOWED_DOCKER_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "ps",
        "stats",
        "logs",
        "inspect",
        "images",
        "network ls",
        "volume ls",
    }
)

# Substrings that must not appear anywhere in a command string (case-sensitive
# as given; callers may run additional normalization).
BLOCKED_PATTERNS: tuple[str, ...] = (
    "sudo",
    "rm ",
    "rmdir",
    "mv ",
    "cp ",
    "chmod",
    "chown",
    "mkfs",
    "dd ",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "kill",
    "pkill",
    "killall",
    "apt",
    "dpkg",
    "pip",
    "npm",
    "useradd",
    "userdel",
    "passwd",
    "crontab",
    "eval",
    "exec",
    ">",
    ">>",
    "| sh",
    "| bash",
    "| dash",
    "$( ",
    "$(",
    "`",
)
