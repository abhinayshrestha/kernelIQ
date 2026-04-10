"""Application-wide configuration (paths, LLM backends, alert defaults).

Values are built from, in order of lowest to highest precedence:

1. Built-in defaults below (edit ``config.py`` for project defaults).
2. Optional ``kerneliq.toml`` in the repo root (same directory as this file),
   under a ``[kerneliq]`` table — copy from ``kerneliq.toml.example``.
3. Environment variables named ``KERNELIQ_<NAME>`` (same spelling as the
   constant, e.g. ``KERNELIQ_MODEL_BACKEND=ollama``).

Python loads this module once per process. After changing overrides, restart
the telemetry daemon and the REPL so imports pick up new values::

    systemctl --user restart kerneliq-daemon
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any


def _coerce_env(raw: str, template: Any) -> Any:
    """Parse ``raw`` from the environment to match ``template``'s type."""
    t = type(template)
    if t is int:
        return int(raw.strip())
    if t is float:
        return float(raw.strip())
    if t is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw


def _default_settings() -> dict[str, Any]:
    """Baseline settings before ``kerneliq.toml`` and env overrides."""
    return {
        "DAEMON_COLLECTION_INTERVAL_SEC": 60,
        "OLLAMA_BASE_URL": "http://192.168.64.1:11434",
        "OLLAMA_CHAT_TIMEOUT_SEC": 120,
        "LOCAL_LLM_MODEL": "llama3.2:3b",
        "MODEL_BACKEND": "ollama",
        # Per-vendor cloud keys (written by the config wizard)
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_MODEL": "",
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": "",
        "GOOGLE_API_KEY": "",
        "GOOGLE_MODEL": "",
        # Derived at load time from the active vendor keys — do not set directly
        "HTTP_LLM_API_KEY": "",
        "HTTP_LLM_CHAT_URL": "",
        "HTTP_LLM_MODEL": "",
        # Alert thresholds
        "ALERT_DISK_WARNING_PERCENT": 80.0,
        "ALERT_DISK_CRITICAL_PERCENT": 95.0,
        "ALERT_MEMORY_FREE_WARNING_PERCENT": 10.0,
        "ALERT_MEMORY_FREE_CRITICAL_PERCENT": 5.0,
        "ALERT_SWAP_USED_WARNING_PERCENT": 80.0,
        "ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT": 80.0,
        "ALERT_CPU_SUSTAINED_SAMPLE_COUNT": 3,
        "ALERT_IOWAIT_THRESHOLD_PERCENT": 20.0,
        "ALERT_IOWAIT_SAMPLE_COUNT": 3,
        "ALERT_ZOMBIE_MIN_COUNT": 5,
        "ALERT_SERVICE_FLAP_WINDOW_MINUTES": 5,
        "ALERT_SERVICE_FLAP_MIN_STATE_CHANGES": 3,
        "ALERT_SERVICE_FLAP_STABILITY_MINUTES": 2,
        "ALERT_OOM_JOURNAL_SINCE": "2 minutes ago",
    }


# Canonical chat endpoints for each cloud backend
_VENDOR_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "openai":   "https://api.openai.com/v1/chat/completions",
    "claude":   "https://api.anthropic.com/v1/messages",
    "google":   "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}

# Default model for each cloud backend when the user leaves the model field empty
_VENDOR_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai":   "gpt-4o",
    "claude":   "claude-sonnet-4-5",
    "google":   "gemini-2.0-flash",
}

# Maps backend name → (api_key_setting, model_setting)
_VENDOR_KEY_MAP: dict[str, tuple[str, str]] = {
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
    "openai":   ("OPENAI_API_KEY",   "OPENAI_MODEL"),
    "claude":   ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
    "google":   ("GOOGLE_API_KEY",   "GOOGLE_MODEL"),
}


def user_config_path() -> Path:
    """Return the path to optional ``kerneliq.toml`` (may not exist)."""
    return Path(__file__).resolve().parent / "kerneliq.toml"


def _merge_user_toml(settings: dict[str, Any]) -> None:
    """Apply ``[kerneliq]`` entries from ``kerneliq.toml`` when present."""
    path = user_config_path()
    if not path.is_file():
        return
    with path.open("rb") as f:
        data = tomllib.load(f)
    blob = data.get("kerneliq", data)
    if not isinstance(blob, dict):
        return
    for key, val in blob.items():
        if key in settings:
            settings[key] = val


def _merge_env(settings: dict[str, Any]) -> None:
    """Override from ``KERNELIQ_<KEY>`` environment variables."""
    for key in list(settings.keys()):
        ev = os.environ.get(f"KERNELIQ_{key}")
        if ev is not None:
            settings[key] = _coerce_env(ev, settings[key])


def _resolve_http_llm(settings: dict[str, Any]) -> None:
    """Derive ``HTTP_LLM_*`` from the active backend's vendor keys.

    This runs after all user overrides are applied so that whatever the user
    set in ``kerneliq.toml`` or env vars flows through to ``model/client.py``
    via the ``HTTP_LLM_*`` constants it imports.
    """
    backend = str(settings.get("MODEL_BACKEND") or "ollama").strip().lower()
    if backend not in _VENDOR_KEY_MAP:
        return  # ollama — no HTTP_LLM_* needed

    api_key_field, model_field = _VENDOR_KEY_MAP[backend]
    api_key = str(settings.get(api_key_field) or settings.get("HTTP_LLM_API_KEY") or "")
    model = str(settings.get(model_field) or settings.get("HTTP_LLM_MODEL") or "")
    url = str(settings.get("HTTP_LLM_CHAT_URL") or "")

    settings["HTTP_LLM_API_KEY"] = api_key
    settings["HTTP_LLM_MODEL"] = model or _VENDOR_DEFAULT_MODELS[backend]
    settings["HTTP_LLM_CHAT_URL"] = url or _VENDOR_URLS[backend]


_cfg = _default_settings()
_merge_user_toml(_cfg)
_merge_env(_cfg)
_resolve_http_llm(_cfg)

DAEMON_COLLECTION_INTERVAL_SEC: int = _cfg["DAEMON_COLLECTION_INTERVAL_SEC"]
"""Seconds between daemon telemetry / process / service collection cycles."""

OLLAMA_BASE_URL: str = _cfg["OLLAMA_BASE_URL"]
"""Root URL for Ollama (scheme, host, port)."""

OLLAMA_CHAT_URL: str = f"{_cfg['OLLAMA_BASE_URL'].rstrip('/')}/api/chat"
"""Ollama ``/api/chat`` endpoint."""

OLLAMA_CHAT_TIMEOUT_SEC: int = _cfg["OLLAMA_CHAT_TIMEOUT_SEC"]
"""Socket timeout in seconds for Ollama chat requests."""

LOCAL_LLM_MODEL: str = _cfg["LOCAL_LLM_MODEL"]
"""Model id for the local Ollama backend."""

MODEL_BACKEND: str = str(_cfg["MODEL_BACKEND"] or "ollama").strip().lower()
"""Active LLM backend: ``ollama`` | ``deepseek`` | ``openai`` | ``claude`` | ``google``."""

# Per-vendor keys (available for direct use if needed)
DEEPSEEK_API_KEY: str = _cfg["DEEPSEEK_API_KEY"]
DEEPSEEK_MODEL: str = _cfg["DEEPSEEK_MODEL"]
OPENAI_API_KEY: str = _cfg["OPENAI_API_KEY"]
OPENAI_MODEL: str = _cfg["OPENAI_MODEL"]
ANTHROPIC_API_KEY: str = _cfg["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL: str = _cfg["ANTHROPIC_MODEL"]
GOOGLE_API_KEY: str = _cfg["GOOGLE_API_KEY"]
GOOGLE_MODEL: str = _cfg["GOOGLE_MODEL"]

# Derived from the active vendor — used by model/client.py
HTTP_LLM_API_KEY: str = _cfg["HTTP_LLM_API_KEY"]
"""API key for the active cloud backend (derived from vendor-specific key)."""

HTTP_LLM_CHAT_URL: str = _cfg["HTTP_LLM_CHAT_URL"]
"""Chat completions URL for the active cloud backend."""

HTTP_LLM_MODEL: str = _cfg["HTTP_LLM_MODEL"]
"""Model id for the active cloud backend."""

ALERT_DISK_WARNING_PERCENT: float = _cfg["ALERT_DISK_WARNING_PERCENT"]
ALERT_DISK_CRITICAL_PERCENT: float = _cfg["ALERT_DISK_CRITICAL_PERCENT"]
ALERT_MEMORY_FREE_WARNING_PERCENT: float = _cfg["ALERT_MEMORY_FREE_WARNING_PERCENT"]
ALERT_MEMORY_FREE_CRITICAL_PERCENT: float = _cfg["ALERT_MEMORY_FREE_CRITICAL_PERCENT"]
ALERT_SWAP_USED_WARNING_PERCENT: float = _cfg["ALERT_SWAP_USED_WARNING_PERCENT"]
ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT: float = _cfg["ALERT_CPU_SUSTAINED_THRESHOLD_PERCENT"]
ALERT_CPU_SUSTAINED_SAMPLE_COUNT: int = _cfg["ALERT_CPU_SUSTAINED_SAMPLE_COUNT"]
ALERT_IOWAIT_THRESHOLD_PERCENT: float = _cfg["ALERT_IOWAIT_THRESHOLD_PERCENT"]
ALERT_IOWAIT_SAMPLE_COUNT: int = _cfg["ALERT_IOWAIT_SAMPLE_COUNT"]
ALERT_ZOMBIE_MIN_COUNT: int = _cfg["ALERT_ZOMBIE_MIN_COUNT"]
ALERT_SERVICE_FLAP_WINDOW_MINUTES: int = _cfg["ALERT_SERVICE_FLAP_WINDOW_MINUTES"]
ALERT_SERVICE_FLAP_MIN_STATE_CHANGES: int = _cfg["ALERT_SERVICE_FLAP_MIN_STATE_CHANGES"]
ALERT_SERVICE_FLAP_STABILITY_MINUTES: int = _cfg["ALERT_SERVICE_FLAP_STABILITY_MINUTES"]
ALERT_OOM_JOURNAL_SINCE: str = _cfg["ALERT_OOM_JOURNAL_SINCE"]

del _cfg
