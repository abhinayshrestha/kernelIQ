"""Append completed investigations to JSONL for training and dataset stats."""

from __future__ import annotations

import json
import os
from pathlib import Path

from db.helpers import query
from db.schema import get_db_path


def _project_root() -> Path:
    """Return the KernelIQ repository root (parent of the ``dataset`` package)."""
    return Path(__file__).resolve().parent.parent


def get_dataset_path() -> str:
    """Return the path to ``training_data.jsonl`` and ensure its directory exists.

    The file lives under the project root at ``dataset/training_data.jsonl``
    (the same layout as ``~/kernelIQ/dataset/training_data.jsonl`` when the
    repo is checked out there).

    Returns:
        Absolute path string to the JSONL file.
    """
    dataset_dir = _project_root() / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    return str(dataset_dir / "training_data.jsonl")


def get_system_context(db_path: str) -> dict:
    """Build a small system snapshot dict from live OS state and latest telemetry.

    Args:
        db_path: Path to the KernelIQ SQLite database.

    Returns:
        Dict with keys ``cpu_cores``, ``total_ram_mb``, ``os``, ``collected_at``.
        Any field that cannot be determined is set to ``None`` instead of
        raising.
    """
    ctx: dict = {
        "cpu_cores": None,
        "total_ram_mb": None,
        "os": None,
        "collected_at": None,
    }
    try:
        n = os.cpu_count()
        ctx["cpu_cores"] = int(n) if n is not None else None
    except (TypeError, ValueError, OSError):
        pass

    try:
        rows = query(
            db_path,
            (
                "SELECT mem_total_mb, timestamp FROM telemetry_samples "
                "ORDER BY timestamp DESC LIMIT 1"
            ),
            (),
        )
        if rows:
            row = rows[0]
            mt = row.get("mem_total_mb")
            ctx["total_ram_mb"] = float(mt) if mt is not None else None
            ctx["collected_at"] = row.get("timestamp")
    except Exception:
        pass

    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("PRETTY_NAME="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                ctx["os"] = val or None
                break
    except OSError:
        pass

    return ctx


def build_training_example(session_id: str, db_path: str) -> dict | None:
    """Load one session from the DB and shape it as a training example dict.

    Args:
        session_id: Investigation session identifier.
        db_path: Path to the KernelIQ SQLite database.

    Returns:
        A dict matching the training schema, or ``None`` if logs or diagnosis
        rows are missing.
    """
    log_rows = query(
        db_path,
        (
            "SELECT step_number, command_type, command_run, output_summary, reasoning "
            "FROM investigation_logs WHERE session_id = ? ORDER BY step_number ASC"
        ),
        (session_id,),
    )
    diag_rows = query(
        db_path,
        (
            "SELECT timestamp, question, observation, evidence_json, action_proposed, "
            "command_proposed, confidence, user_feedback "
            "FROM diagnoses WHERE session_id = ? ORDER BY id DESC LIMIT 1"
        ),
        (session_id,),
    )
    if not log_rows or not diag_rows:
        return None

    d = diag_rows[0]
    evidence_raw = d.get("evidence_json") or ""
    try:
        evidence_str = json.loads(evidence_raw)
        if not isinstance(evidence_str, str):
            evidence_str = str(evidence_str)
    except (json.JSONDecodeError, TypeError):
        evidence_str = str(evidence_raw)

    cmd_prop = d.get("command_proposed")
    command_proposed = "" if cmd_prop is None else str(cmd_prop)

    conf = d.get("confidence")
    try:
        confidence_f = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        confidence_f = 0.0

    steps: list[dict] = []
    for row in log_rows:
        sn = row.get("step_number")
        try:
            step_number = int(sn) if sn is not None else 0
        except (TypeError, ValueError):
            step_number = 0
        ctype = row.get("command_type") or "command"
        steps.append(
            {
                "step_number": step_number,
                "type": str(ctype),
                "input": row.get("command_run") or "",
                "output": row.get("output_summary") or "",
                "reasoning": row.get("reasoning") or "",
            }
        )

    return {
        "session_id": session_id,
        "timestamp": d.get("timestamp") or "",
        "question": d.get("question") or "",
        "model_backend": "deepseek",
        "investigation_steps": steps,
        "final_response": {
            "observation": d.get("observation") or "",
            "evidence": evidence_str,
            "action": d.get("action_proposed") or "",
            "confidence": confidence_f,
            "command_proposed": command_proposed,
        },
        "user_feedback": d.get("user_feedback"),
        "system_context": get_system_context(db_path),
    }


def save_example(example: dict) -> None:
    """Append one training example as a single JSON line to the dataset file.

    Args:
        example: Serializable dict from :func:`build_training_example`.
    """
    path = get_dataset_path()
    line = json.dumps(example, ensure_ascii=False, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def log_investigation(session_id: str) -> bool:
    """Record a completed investigation to the JSONL dataset when appropriate.

    Skips low-confidence rows (below 50%) and rows marked ``user_feedback ==
    \"wrong\"``.

    Args:
        session_id: Session id of the investigation just stored in the DB.

    Returns:
        ``True`` if an example was appended, ``False`` otherwise.
    """
    db_path = get_db_path()
    example = build_training_example(session_id, db_path)
    if example is None:
        return False

    conf = float(example["final_response"]["confidence"])
    if conf < 50.0:
        return False

    feedback = example.get("user_feedback")
    if feedback == "wrong":
        return False

    save_example(example)
    return True


def get_dataset_stats() -> dict:
    """Scan ``training_data.jsonl`` and aggregate simple statistics.

    Returns:
        Dict with ``total_examples``, ``avg_confidence``, ``by_feedback``,
        ``by_backend``, and ``date_range`` (``earliest`` / ``latest``).
        If the file is missing or empty, counts are zero and aggregates empty.
    """
    empty: dict = {
        "total_examples": 0,
        "avg_confidence": 0.0,
        "by_feedback": {},
        "by_backend": {},
        "date_range": {"earliest": None, "latest": None},
    }
    path = get_dataset_path()
    if not Path(path).is_file():
        return empty

    total = 0
    conf_sum = 0.0
    conf_n = 0
    by_feedback: dict = {}
    by_backend: dict = {}
    earliest: str | None = None
    latest: str | None = None

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ex = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                fr = ex.get("final_response") or {}
                c = fr.get("confidence")
                try:
                    cf = float(c) if c is not None else None
                except (TypeError, ValueError):
                    cf = None
                if cf is not None:
                    conf_sum += cf
                    conf_n += 1

                fb = ex.get("user_feedback")
                by_feedback[fb] = by_feedback.get(fb, 0) + 1

                be = ex.get("model_backend") or "unknown"
                by_backend[be] = by_backend.get(be, 0) + 1

                ts = ex.get("timestamp")
                if isinstance(ts, str) and ts:
                    if earliest is None or ts < earliest:
                        earliest = ts
                    if latest is None or ts > latest:
                        latest = ts
    except OSError:
        return empty

    avg = conf_sum / conf_n if conf_n else 0.0
    return {
        "total_examples": total,
        "avg_confidence": avg,
        "by_feedback": by_feedback,
        "by_backend": by_backend,
        "date_range": {"earliest": earliest, "latest": latest},
    }
