"""HTTP client for Ollama or any supported cloud backend (streaming and not).

Backend is selected by :data:`config.MODEL_BACKEND`; URLs, keys, and timeouts
are resolved at import time by :mod:`config` from ``kerneliq.toml``.

Supported backends:
  - ``ollama``   — local Ollama NDJSON streaming API
  - ``deepseek`` — OpenAI-compatible REST API (Chat Completions)
  - ``openai``   — OpenAI REST API; Chat Completions for GPT-4o and earlier,
                    Responses API (``/v1/responses``) for GPT-5.x models
  - ``google``   — Google Gemini via OpenAI-compatible endpoint
  - ``claude``   — Anthropic Messages API (different auth + response format)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Generator
from urllib.parse import urlparse

from config import (
    HTTP_LLM_API_KEY,
    HTTP_LLM_CHAT_URL,
    HTTP_LLM_MODEL,
    LOCAL_LLM_MODEL,
    MODEL_BACKEND,
    OLLAMA_BASE_URL,
    OLLAMA_CHAT_TIMEOUT_SEC,
    OLLAMA_CHAT_URL,
)

# Backends that use the OpenAI-compatible chat completions format
_OPENAI_COMPAT_BACKENDS = {"deepseek", "openai", "google"}
# All cloud backends (non-Ollama)
_CLOUD_BACKENDS = {"deepseek", "openai", "google", "claude"}

# OpenAI Responses API for GPT-5.x models
_OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
_RESPONSES_API_PREFIXES = ("gpt-5",)


def _use_responses_api() -> bool:
    """Return True when the active model requires the OpenAI Responses API.

    GPT-5.x models perform better on — and in some configurations
    require — the ``/v1/responses`` endpoint instead of the legacy
    ``/v1/chat/completions``.
    """
    if MODEL_BACKEND != "openai":
        return False
    return any(
        HTTP_LLM_MODEL.strip().lower().startswith(p)
        for p in _RESPONSES_API_PREFIXES
    )


def _ollama_connection_error_text() -> str:
    """Human-readable connection error including host:port from :data:`config.OLLAMA_BASE_URL`."""
    netloc = urlparse(OLLAMA_BASE_URL).netloc
    target = netloc if netloc else OLLAMA_BASE_URL
    return f"ERROR: Could not connect to Ollama at {target}"


def _http_llm_connection_error_text() -> str:
    """Human-readable connection error for the HTTP OpenAI-compatible API."""
    netloc = urlparse(HTTP_LLM_CHAT_URL).netloc
    target = netloc if netloc else HTTP_LLM_CHAT_URL
    return f"ERROR: Could not connect to HTTP LLM API at {target}"


def _http_llm_http_error_text(exc: urllib.error.HTTPError) -> str:
    """Format an HTTP error from the chat API, including JSON body when present."""
    raw_body = b""
    try:
        raw_body = exc.read()
    except Exception:
        pass
    text = raw_body.decode("utf-8", errors="replace")[:4000]
    api_msg = ""
    try:
        data = json.loads(text) if text.strip() else {}
        err = data.get("error")
        if isinstance(err, dict):
            api_msg = str(err.get("message") or "").strip()
    except (json.JSONDecodeError, TypeError):
        pass

    code = int(exc.code) if exc.code is not None else 0
    if api_msg:
        line = f"ERROR: HTTP {code}: {api_msg}"
    else:
        line = f"ERROR: HTTP Error {code}: {exc.reason}"

    if code == 429:
        line += (
            " | Rate limit or quota (OpenAI): wait a minute, check usage and "
            "billing at https://platform.openai.com, or use a higher tier. "
            "KernelIQ sends many API calls per investigation."
        )
    elif code == 401:
        line += " | Invalid or expired API key."
    elif code == 403:
        line += " | Access denied (model not enabled for this key or region)."

    return line


def _content_chunk_from_ndjson_line(line_bytes: bytes) -> str | None:
    """Parse one NDJSON line from Ollama stream; return assistant content or None."""
    line = line_bytes.strip()
    if not line:
        return None
    try:
        obj = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    msg = obj.get("message") or {}
    piece = msg.get("content")
    if not piece:
        return None
    return str(piece)


def _http_llm_sse_line_payload(line: str) -> str | None:
    """Return SSE payload string after ``data:``, or None if not a data line."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    return line[5:].lstrip()


def _http_llm_sse_delta_from_payload(payload: str) -> tuple[str | None, bool]:
    """Parse one OpenAI-style SSE ``data:`` payload.

    Returns:
        (content_chunk_or_none, is_done). ``is_done`` is True for ``[DONE]``.
    """
    if payload == "[DONE]":
        return None, True
    if not payload:
        return None, False
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None, False
    choices = obj.get("choices") or []
    if not choices:
        return None, False
    delta = choices[0].get("delta") or {}
    piece = delta.get("content")
    if not piece:
        return None, False
    return str(piece), False


def _responses_api_sse_delta(payload: str) -> tuple[str | None, bool]:
    """Parse one SSE ``data:`` payload from the OpenAI Responses API.

    Returns:
        (text_chunk_or_none, is_done).
        ``is_done`` is True on ``response.completed`` or ``response.failed``.
    """
    if not payload or payload == "[DONE]":
        return None, payload == "[DONE]"
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None, False
    evt_type = obj.get("type", "")
    if evt_type == "response.output_text.delta":
        piece = obj.get("delta")
        return (str(piece) if piece else None), False
    if evt_type in ("response.completed", "response.failed"):
        return None, True
    return None, False


def _claude_request(
    messages: list[dict],
    stream: bool,
    temperature: float,
    timeout: float,
) -> urllib.request.Request:
    """Build an Anthropic Messages API request."""
    system = ""
    filtered: list[dict] = []
    for m in messages:
        if m.get("role") == "system":
            system = str(m.get("content") or "")
        else:
            filtered.append(m)
    payload: dict = {
        "model": HTTP_LLM_MODEL,
        "max_tokens": 4096,
        "messages": filtered,
        "stream": stream,
    }
    if system:
        payload["system"] = system
    if temperature != 1.0:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        HTTP_LLM_CHAT_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": HTTP_LLM_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )


def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
    timeout_sec: float | None = None,
) -> str:
    """Send a non-streaming chat request and return the assistant message text.

    Args:
        messages: Message list, each ``{"role": ..., "content": ...}``.
        model: Model name when using Ollama; ``None`` uses :data:`config.LOCAL_LLM_MODEL`.
            Ignored for cloud backends (uses :data:`config.HTTP_LLM_MODEL`).
        temperature: Sampling temperature.
        timeout_sec: Socket timeout in seconds; defaults to
            :data:`config.OLLAMA_CHAT_TIMEOUT_SEC` when ``None``.

    Returns:
        Assistant content string, or an ``ERROR: ...`` string on failure.
    """
    timeout = OLLAMA_CHAT_TIMEOUT_SEC if timeout_sec is None else float(timeout_sec)

    if _use_responses_api():
        payload: dict = {
            "model": HTTP_LLM_MODEL,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": 4096,
            "stream": False,
            "store": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _OPENAI_RESPONSES_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {HTTP_LLM_API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return _http_llm_http_error_text(exc)
        except urllib.error.URLError:
            return _http_llm_connection_error_text()
        except Exception as exc:
            return f"ERROR: {exc}"

        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"ERROR: {exc}"

        text = data.get("output_text")
        if text:
            return str(text)
        for item in data.get("output") or []:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        return str(part.get("text") or "")
        return ""

    if MODEL_BACKEND in _OPENAI_COMPAT_BACKENDS:
        payload = {
            "model": HTTP_LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            HTTP_LLM_CHAT_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {HTTP_LLM_API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return _http_llm_http_error_text(exc)
        except urllib.error.URLError:
            return _http_llm_connection_error_text()
        except Exception as exc:
            return f"ERROR: {exc}"

        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"ERROR: {exc}"

        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if content is None:
            return ""
        return str(content)

    if MODEL_BACKEND == "claude":
        req = _claude_request(messages, stream=False, temperature=temperature, timeout=timeout)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return _http_llm_http_error_text(exc)
        except urllib.error.URLError:
            return _http_llm_connection_error_text()
        except Exception as exc:
            return f"ERROR: {exc}"

        try:
            data = json.loads(raw)
        except Exception as exc:
            return f"ERROR: {exc}"

        for block in data.get("content") or []:
            if block.get("type") == "text":
                return str(block.get("text") or "")
        return ""

    # Ollama
    local_model = LOCAL_LLM_MODEL if model is None else model
    payload = {
        "model": local_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return f"ERROR: {exc}"
    except urllib.error.URLError:
        return _ollama_connection_error_text()
    except Exception as exc:
        return f"ERROR: {exc}"

    try:
        data = json.loads(raw)
    except Exception as exc:
        return f"ERROR: {exc}"

    message = data.get("message") or {}
    content = message.get("content")
    if content is None:
        return ""
    return str(content)


def chat_stream(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.3,
) -> Generator[str, None, None]:
    """Stream assistant tokens from Ollama (NDJSON) or HTTP API (SSE).

    Yields each non-empty content fragment. On connection failure, yields one
    error string and stops.

    Args:
        messages: Same shape as :func:`chat`.
        model: Model name when using Ollama; ``None`` uses :data:`config.LOCAL_LLM_MODEL`.
            Ignored when backend is HTTP (``deepseek``).
        temperature: Sampling temperature.
    """
    if _use_responses_api():
        payload: dict = {
            "model": HTTP_LLM_MODEL,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": 4096,
            "stream": True,
            "store": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _OPENAI_RESPONSES_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {HTTP_LLM_API_KEY}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=OLLAMA_CHAT_TIMEOUT_SEC)
        except urllib.error.HTTPError as exc:
            yield _http_llm_http_error_text(exc)
            return
        except urllib.error.URLError:
            yield _http_llm_connection_error_text()
            return
        except Exception as exc:
            yield f"ERROR: {exc}"
            return

        try:
            buffer = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    sse_payload = _http_llm_sse_line_payload(text)
                    if sse_payload is None:
                        continue
                    piece, done = _responses_api_sse_delta(sse_payload)
                    if done:
                        resp.close()
                        return
                    if piece:
                        yield piece
            resp.close()
        except Exception as exc:
            yield f"ERROR: {exc}"
        return

    if MODEL_BACKEND in _OPENAI_COMPAT_BACKENDS:
        payload = {
            "model": HTTP_LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
            "stream": True,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            HTTP_LLM_CHAT_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {HTTP_LLM_API_KEY}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=OLLAMA_CHAT_TIMEOUT_SEC)
        except urllib.error.HTTPError as exc:
            yield _http_llm_http_error_text(exc)
            return
        except urllib.error.URLError:
            yield _http_llm_connection_error_text()
            return
        except Exception as exc:
            yield f"ERROR: {exc}"
            return

        try:
            buffer = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    payload = _http_llm_sse_line_payload(text)
                    if payload is None:
                        continue
                    piece, done = _http_llm_sse_delta_from_payload(payload)
                    if done:
                        resp.close()
                        return
                    if piece:
                        yield piece
            tail_line = buffer.decode("utf-8", errors="replace").rstrip("\r")
            payload = _http_llm_sse_line_payload(tail_line)
            if payload:
                piece, done = _http_llm_sse_delta_from_payload(payload)
                if not done and piece:
                    yield piece
            resp.close()
        except Exception as exc:
            yield f"ERROR: {exc}"
        return

    if MODEL_BACKEND == "claude":
        req = _claude_request(messages, stream=True, temperature=temperature,
                              timeout=OLLAMA_CHAT_TIMEOUT_SEC)
        try:
            resp = urllib.request.urlopen(req, timeout=OLLAMA_CHAT_TIMEOUT_SEC)
        except urllib.error.HTTPError as exc:
            yield _http_llm_http_error_text(exc)
            return
        except urllib.error.URLError:
            yield _http_llm_connection_error_text()
            return
        except Exception as exc:
            yield f"ERROR: {exc}"
            return

        # Anthropic SSE: event lines followed by data lines
        # data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}
        try:
            buffer = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text.startswith("data:"):
                        continue
                    raw_payload = text[5:].lstrip()
                    if raw_payload in ("[DONE]", ""):
                        continue
                    try:
                        obj = json.loads(raw_payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "message_stop":
                        resp.close()
                        return
                    delta = obj.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        piece = delta.get("text")
                        if piece:
                            yield str(piece)
            resp.close()
        except Exception as exc:
            yield f"ERROR: {exc}"
        return

    local_model = LOCAL_LLM_MODEL if model is None else model
    payload = {
        "model": local_model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=OLLAMA_CHAT_TIMEOUT_SEC)
    except urllib.error.HTTPError as exc:
        yield f"ERROR: {exc}"
        return
    except urllib.error.URLError:
        yield _ollama_connection_error_text()
        return
    except Exception as exc:
        yield f"ERROR: {exc}"
        return

    try:
        buffer = b""
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                piece = _content_chunk_from_ndjson_line(line)
                if piece:
                    yield piece
        tail = _content_chunk_from_ndjson_line(buffer)
        if tail:
            yield tail
        resp.close()
    except Exception as exc:
        yield f"ERROR: {exc}"


def is_ollama_available(timeout: int = 5) -> bool:
    """Return whether the configured LLM backend is usable.

    For cloud backends, returns True if an API key is configured.
    For Ollama, performs a GET to the root URL and checks HTTP 200.

    Args:
        timeout: Socket timeout in seconds for the Ollama health check.

    Returns:
        ``True`` if the backend appears available, else ``False``.
    """
    if MODEL_BACKEND in _CLOUD_BACKENDS:
        return bool(HTTP_LLM_API_KEY and str(HTTP_LLM_API_KEY).strip())

    url = f"{OLLAMA_BASE_URL.rstrip('/')}/"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False
