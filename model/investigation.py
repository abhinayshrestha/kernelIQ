"""Multi-step investigation loop: the LLM runs read-only commands and SQL until diagnosis."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from db.helpers import insert_diagnosis, insert_investigation_log
from db.schema import get_db_path
from dataset.logger import log_investigation
from executor.runner import run_command
from executor.sql_runner import run_query
from executor.sql_runner import _format_sql_result as _fmt_sql
from config import MODEL_BACKEND
from model.client import chat
from model.condenser import condense_command
from model.context_window import ContextWindow, StepEntry
from model.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

_MAX_LOG_SUMMARY_CHARS = 8000
_THINKING_SUMMARY_CHARS = 200


def _utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 text."""
    return datetime.now(timezone.utc).isoformat()


def _has_diagnosis(text: str) -> bool:
    """Return True if the assistant text includes a DIAGNOSIS marker."""
    return bool(re.search(r"\bDIAGNOSIS\s*:", text, re.IGNORECASE))


def _extract_labeled_line(text: str, label: str) -> str | None:
    """Return the payload after ``LABEL:`` on the first matching line, or None."""
    prefix = f"{label}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(prefix.upper()):
            return stripped.split(":", 1)[1].strip() if ":" in stripped else ""
    return None


def _truncate_for_log(s: str, max_chars: int = _MAX_LOG_SUMMARY_CHARS) -> str:
    """Truncate long strings for ``investigation_logs.output_summary``."""
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "\n... [truncated]"


def _format_command_result(result: dict[str, Any]) -> str:
    if not result.get("success"):
        error = result.get("error", "unknown error")
        return f"Command failed: {error}"

    out = (result.get("output") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    rc = result.get("return_code", 0)

    if not out and not stderr:
        return f"Command succeeded with no output (exit_code={rc})"

    parts = []
    if out:
        parts.append(out)
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    parts.append(f"exit_code={rc}")

    return "\n".join(parts).strip()



def _normalize_evidence_bullet_lines(evidence: str) -> str:
    """Normalize ``* `` list markers to ``- `` in an Evidence block."""
    if not evidence:
        return evidence
    lines: list[str] = []
    for line in evidence.splitlines():
        if re.match(r"^\s*\*\s+", line):
            line = re.sub(r"^(\s*)\*\s+", r"\1- ", line, count=1)
        lines.append(line)
    return "\n".join(lines)


def _strip_diagnosis_markdown(s: str) -> str:
    """Remove common markdown (``**``, ``##``, fenced ```) from parsed section text."""
    if not s:
        return s
    out = s

    def _fence_body(m: re.Match[str]) -> str:
        return m.group(1).strip()

    out = re.sub(
        r"```[a-zA-Z0-9]*\s*\n?(.*?)```",
        _fence_body,
        out,
        flags=re.DOTALL,
    )
    out = re.sub(r"(?m)^\s*```\s*$", "", out)
    out = re.sub(r"\*{2,}", "", out)
    out = re.sub(r"(?m)^#{1,6}\s*", "", out)
    return out.strip()


def _normalize_markdown_headings_for_parse(work: str) -> str:
    """Map ``## Observation``-style lines to plain headings so section patterns match."""
    out = re.sub(
        r"(?mi)^(\s*)#{1,6}\s*(Observation|Evidence|Action)\s*$",
        r"\1\2",
        work,
    )
    out = re.sub(
        r"(?mi)^(\s*)#{1,6}\s*(Observation|Evidence|Action)\s*:\s*",
        r"\1\2: ",
        out,
    )
    return out


def _slice_observation_inline(work: str) -> str:
    """Parse ``Observation:`` with text on the same line; body until Evidence heading."""
    m = re.search(
        r"(?ims)^\s*Observation\s*:\s*(.*?)(?=^\s*Evidence\s*(?:$|:))",
        work,
    )
    if not m:
        return ""
    return m.group(1).strip()


def _parse_diagnosis_text(raw: str) -> dict[str, Any]:
    """Parse structured DIAGNOSIS sections from model text.

    Accepts the canonical layout (section titles alone on a line), inline
    ``Observation:`` on one line, ``*`` or ``-`` evidence bullets, and bodies
    with or without a leading ``DIAGNOSIS:`` marker. Strips ``**``, line-leading
    ``##``, and fenced ``` from parsed section strings.

    On missing sections or malformed output, returns a low-confidence fallback
    with an explanatory observation and the original text preserved in
    ``raw_response``.

    Args:
        raw: Full assistant message (may include ``DIAGNOSIS:`` header).

    Returns:
        Dict with keys ``observation``, ``evidence``, ``action``, ``confidence``,
        ``command_proposed`` (str, possibly empty), and ``raw_response``.
    """
    if not raw or not str(raw).strip():
        return {
            "observation": "The model returned an empty reply; no diagnosis could be parsed.",
            "evidence": "",
            "action": "",
            "confidence": 0.0,
            "command_proposed": "",
            "raw_response": raw or "",
        }

    text = str(raw).strip()
    if text.upper().startswith("ERROR:"):
        if MODEL_BACKEND == "ollama":
            hint = (
                "Check that Ollama is running and that OLLAMA_BASE_URL is correct, "
                "then restart the REPL and retry."
            )
        elif "429" in text or "rate limit" in text.lower():
            hint = (
                "Provider rate limit or quota. Wait and retry, reduce how often you "
                "run investigations, check billing/limits on the API dashboard, or "
                "switch MODEL_BACKEND to local Ollama for development."
            )
        else:
            hint = (
                "For OpenAI, use HTTP_LLM_CHAT_URL "
                "https://api.openai.com/v1/chat/completions. "
                "Verify HTTP_LLM_API_KEY and HTTP_LLM_MODEL; restart the REPL."
            )
        return {
            "observation": (
                "The chat backend returned an error; see Backend detail below."
            ),
            "evidence": "",
            "action": hint,
            "confidence": 0.0,
            "command_proposed": "",
            "raw_response": text,
        }

    work = text
    m_diag = re.search(r"DIAGNOSIS\s*:", work, re.IGNORECASE)
    if m_diag:
        work = work[m_diag.end() :].lstrip()
    # If DIAGNOSIS: is absent, parse from full text when sections are present.

    work_struct = _normalize_markdown_headings_for_parse(work)

    def _slice_after_heading(src: str, heading: str, end_heading: str) -> str:
        start_pat = re.compile(rf"^{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
        end_pat = re.compile(rf"^\s*{re.escape(end_heading)}\s*$", re.IGNORECASE | re.MULTILINE)
        ms = start_pat.search(src)
        if not ms:
            return ""
        start = ms.end()
        me = end_pat.search(src, start)
        if not me:
            return src[start:].strip()
        return src[start : me.start()].strip()

    observation = _slice_after_heading(work_struct, "Observation", "Evidence")
    if not observation.strip():
        observation = _slice_observation_inline(work_struct)

    evidence = _slice_after_heading(work_struct, "Evidence", "Action")
    evidence = _normalize_evidence_bullet_lines(evidence)

    action = ""
    ma = re.search(r"^\s*Action\s*$", work_struct, re.IGNORECASE | re.MULTILINE)
    if ma:
        start = ma.end()
        mc = re.search(r"Confidence\s*:", work_struct[start:], re.IGNORECASE)
        if mc:
            action = work_struct[start : start + mc.start()].strip()
        else:
            action = work_struct[start:].strip()

    confidence = 0.0
    mc = re.search(r"Confidence\s*:\s*([\d.]+)\s*%", work_struct, re.IGNORECASE)
    if mc:
        try:
            confidence = float(mc.group(1))
        except ValueError:
            confidence = 0.0

    command_proposed = ""
    mcmd = re.search(r"Command\s*:\s*(.+)", work_struct, re.IGNORECASE)
    if mcmd:
        cmd_line = mcmd.group(1).strip()
        if "Proceed?" in cmd_line:
            cmd_line = cmd_line.split("Proceed?", 1)[0].strip()
        command_proposed = cmd_line

    observation = _strip_diagnosis_markdown(observation)
    evidence = _strip_diagnosis_markdown(evidence)
    action = _strip_diagnosis_markdown(action)

    if not observation and not evidence and not action:
        return {
            "observation": (
                "Could not parse Observation / Evidence / Action from the model output."
            ),
            "evidence": _truncate_for_log(work, 2000),
            "action": "",
            "confidence": 0.0,
            "command_proposed": command_proposed,
            "raw_response": text,
        }

    return {
        "observation": observation,
        "evidence": evidence,
        "action": action,
        "confidence": confidence,
        "command_proposed": command_proposed,
        "raw_response": text,
    }


def investigate(
    question: str,
    on_step: Callable[[str, str], None] | None = None,
    max_steps: int = 10,
) -> dict[str, Any]:
    """Run the investigation loop until a DIAGNOSIS or step limit.

    The model receives the system prompt and the user question, then repeatedly
    emits ``COMMAND:``, ``SQL:``, or a final ``DIAGNOSIS:`` block. Each command
    or query is executed, logged to ``investigation_logs``, and the output is
    sent back as a user message. If no diagnosis appears within ``max_steps``
    chat turns, one final user nudge asks for a DIAGNOSIS with available evidence.

    Args:
        question: The user's natural-language problem description.
        on_step: Optional callback ``(step_type, detail)`` with ``step_type``
            one of ``\"command\"``, ``\"sql\"``, or ``\"thinking\"``.
        max_steps: Maximum number of :func:`~model.client.chat` calls before
            forcing a concluding DIAGNOSIS attempt.

    Returns:
        Dict with ``session_id``, ``question``, ``observation``, ``evidence``,
        ``action``, ``confidence``, ``command_proposed``, and ``raw_response``.
    """
    session_id = str(uuid.uuid4())
    db_path = get_db_path()

    window = ContextWindow(question=question)
    last_assistant_msg: str | None = None

    step_number = 0
    final_raw = ""

    seen_commands: set[str] = set()
    seen_sql: set[str] = set()

    for _ in range(max_steps):
        messages = window.build_messages(last_assistant_msg)
        # logger.debug("messages:\n%s", json.dumps(messages, indent=2))
        response = chat(messages)
        final_raw = response

        if response.strip().upper().startswith("ERROR:"):
            break

        if _has_diagnosis(response):
            break
        cmd = _extract_labeled_line(response, "COMMAND")
        if cmd is not None:
            step_number += 1
            if on_step is not None:
                on_step("command", cmd)

            if cmd in seen_commands:
                window.add_step(StepEntry(
                    step_number=step_number,
                    command_type="command",
                    command_run=cmd,
                    digest=(
                        "- Command already run in a previous step. "
                        "Result unchanged. Do not repeat this command."
                    ),
                ))
                last_assistant_msg = None
                try:
                    insert_investigation_log(
                        db_path,
                        {
                            "session_id": session_id,
                            "timestamp": _utc_now_iso(),
                            "question": question,
                            "step_number": step_number,
                            "command_type": "command",
                            "command_run": cmd,
                            "output_summary": (
                                "Skipped — duplicate command."
                            ),
                            "reasoning": response[:300],
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "insert_investigation_log failed: %s", exc
                    )
                continue

            seen_commands.add(cmd)
            result = run_command(cmd)
            raw_output = _format_command_result(result)
            digest = condense_command(cmd, raw_output)
            window.add_step(StepEntry(
                step_number=step_number,
                command_type="command",
                command_run=cmd,
                digest=digest,
            ))
            last_assistant_msg = None
            try:
                insert_investigation_log(
                    db_path,
                    {
                        "session_id": session_id,
                        "timestamp": _utc_now_iso(),
                        "question": question,
                        "step_number": step_number,
                        "command_type": "command",
                        "command_run": cmd,
                        "output_summary": digest,
                        "reasoning": response[:300],
                    },
                )
            except Exception as exc:
                logger.warning("insert_investigation_log failed: %s", exc)
            continue

        sql = _extract_labeled_line(response, "SQL")
        if sql is not None:
            step_number += 1
            if on_step is not None:
                on_step("sql", sql)

            if sql in seen_sql:
                window.add_step(StepEntry(
                    step_number=step_number,
                    command_type="sql",
                    command_run=sql,
                    digest=(
                        "- SQL query already run in a previous step. "
                        "Result unchanged. Query the same table only if you "
                        "need a different time range or columns not yet retrieved."
                    ),
                ))
                last_assistant_msg = None
                try:
                    insert_investigation_log(
                        db_path,
                        {
                            "session_id": session_id,
                            "timestamp": _utc_now_iso(),
                            "question": question,
                            "step_number": step_number,
                            "command_type": "sql",
                            "command_run": sql,
                            "output_summary": (
                                "Skipped — duplicate SQL query."
                            ),
                            "reasoning": response[:300],
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "insert_investigation_log failed: %s", exc
                    )
                continue

            seen_sql.add(sql)
            result = run_query(db_path, sql)
            formatted = _fmt_sql(result)
            # SQL output is already clean tabular data after formatting.
            # The formatter handles all condensation — no extra LLM call needed.
            digest = formatted
            window.add_step(StepEntry(
                step_number=step_number,
                command_type="sql",
                command_run=sql,
                digest=digest,
            ))
            last_assistant_msg = None
            try:
                insert_investigation_log(
                    db_path,
                    {
                        "session_id": session_id,
                        "timestamp": _utc_now_iso(),
                        "question": question,
                        "step_number": step_number,
                        "command_type": "sql",
                        "command_run": sql,
                        "output_summary": digest,
                        "reasoning": response[:300],
                    },
                )
            except Exception as exc:
                logger.warning("insert_investigation_log failed: %s", exc)
            continue

        if on_step is not None:
            summary = response.strip().replace("\n", " ")[:_THINKING_SUMMARY_CHARS]
            on_step("thinking", summary or "(empty)")
        last_assistant_msg = response

    if not _has_diagnosis(final_raw):
        final_messages = window.build_messages(last_assistant_msg)
        final_messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum investigation steps. "
                "Provide your DIAGNOSIS: now with whatever evidence "
                "you have."
            ),
        })
        final_raw = chat(final_messages)

    parsed = _parse_diagnosis_text(final_raw)
    observation = str(parsed["observation"])
    evidence = str(parsed["evidence"])
    action = str(parsed["action"])
    confidence = float(parsed["confidence"])
    command_proposed = str(parsed.get("command_proposed") or "")
    raw_response = str(parsed["raw_response"])

    evidence_json = json.dumps(evidence)
    diagnosis_ts = _utc_now_iso()

    try:
        insert_diagnosis(
            db_path,
            {
                "session_id": session_id,
                "timestamp": diagnosis_ts,
                "question": question,
                "observation": observation,
                "evidence_json": evidence_json,
                "action_proposed": action,
                "command_proposed": command_proposed or None,
                "confidence": confidence,
            },
        )
    except Exception as exc:
        logger.warning("insert_diagnosis failed: %s", exc)
    else:
        try:
            log_investigation(session_id)
        except Exception as exc:
            logger.warning("dataset logging failed: %s", exc)

    return {
        "session_id": session_id,
        "question": question,
        "observation": observation,
        "evidence": evidence,
        "action": action,
        "confidence": confidence,
        "command_proposed": command_proposed if command_proposed else None,
        "raw_response": raw_response,
    }
